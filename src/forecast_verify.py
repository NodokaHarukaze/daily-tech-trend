# src/forecast_verify.py
"""過去の未来予測を現在のニュースと照合し、的中/外れ/未確定を判定する"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from db import connect, init_db
from llm_insights_api import post_ollama, _get_lm_content
from forecast_generate import build_news_digest, FORECAST_MODEL, CAT_LABELS
from forecast_parser import parse_forecast_markdown, parse_prediction_items

# 時間軸ごとの検証スケジュール: [(経過日数, ラウンド番号), ...]
HORIZON_VERIFY_SCHEDULE = {
    "1週間後":    [(7, 1)],
    "1〜6ヶ月後": [(30, 1), (90, 2), (180, 3)],
    "1年後":      [(180, 1), (365, 2)],
}

# 時間軸ごとのニュースダイジェスト参照期間（時間）
DIGEST_HOURS = {
    "1週間後":    72,
    "1〜6ヶ月後": 168,
    "1年後":      336,
}

VERIFY_SYSTEM = """\
あなたはニュース分析の専門家です。
過去の未来予測が、現在のニュースで的中したか・外れたか・まだ判定不能かを検証してください。
出力はJSONのみ。JSON以外の文字を一切出さないでください。
必ず日本語で出力してください。"""

VERIFY_USER_TMPL = """\
以下は{report_date}に生成された「{horizon}」の未来予測です。

{predictions_text}

以下は現在のニュースダイジェストです。

{current_digest}

各予測について、現在のニュースをもとに検証し、以下のJSON形式で出力してください。
的中・部分的中の場合は、根拠となった記事タイトルを evidence_title に、可能なら evidence_source に記入してください。
[
  {{
    "title": "予測タイトル（元の予測から転記）",
    "verdict": "的中|外れ|未確定",
    "reason": "判定理由（100字以内）",
    "accuracy": 0.0〜1.0の数値（的中度。的中=1.0, 部分的中=0.5, 外れ=0.0, 未確定=null）,
    "evidence_title": "的中/部分的中の根拠となった記事タイトル（該当時のみ、なければ空文字）",
    "evidence_source": "その記事のソース名（該当時のみ、なければ空文字）"
  }}
]"""


def _extract_json_array(text: str) -> str | None:
    """テキストから最初の JSON 配列を、括弧の深さを追跡して抽出する。

    `llm_insights_api._extract_json_object`（`{`/`}` 版）と同じ発想。
    素朴な `find("[")`/`rfind("]")` だと、モデルが JSON の後に雑談文を続けて
    その中に `]` を含めた場合（例:「上記の予測[的中]について」）や、
    文字列値の中に無関係な `]` がある場合に、実際の配列とは違う位置を
    終端とみなして `json.loads` が失敗する。深さ0に戻った時点の `]` を
    確定の終端として使うことで、末尾の余計な文字列に引きずられなくする。
    """
    if not text:
        return None
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("[")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(t)):
        c = t[i]
        if escape_next:
            escape_next = False
            continue
        if c == '\\' and in_string:
            escape_next = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return t[start:i + 1]
    return None


def _try_parse_verdict_json(raw_text: str) -> list | None:
    """LLM 応答から JSON 配列を抽出。失敗時は None を返す（空配列との区別が必要なため）。"""
    candidate = _extract_json_array(raw_text)
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _call_verify_llm(system: str, user: str, max_retries: int = 2) -> list | None:
    """検証用LLM呼び出し。JSON 解析失敗時はプロンプトを補強して再試行する。

    リトライ全滅時は **None** を返す。空配列 [] は「LLM が確かに判定不能だった」を意味し、
    None は「LLM 応答を解析できなかった」を区別するために返す（呼び出し側で既存 verdict を
    保持するか上書きするかを判断する材料）。

    response_format（JSON-mode）は付けない:
    - gpt-oss:20b では受理されず逆に応答品質が落ちることを E2E で確認したため
    - system prompt で「JSONのみ」を強く指示する方が実用的に安定
    """
    base_payload = {
        "model": FORECAST_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 2000,
    }
    for attempt in range(max_retries + 1):
        try:
            payload = dict(base_payload)
            # 2回目以降は system に補強メッセージを追記
            if attempt > 0:
                payload["messages"] = [
                    {"role": "system",
                     "content": system + "\n\n前回の応答が JSON 配列として解析できませんでした。"
                                "余計な文字を一切付けず、[ で始まり ] で終わる JSON 配列のみを返してください。"},
                    {"role": "user", "content": user},
                ]
            resp = post_ollama(payload, timeout=180, retries=2)
        except Exception as e:
            print(f"  [WARN] verify LLM 呼び出し失敗 (試行{attempt+1}/{max_retries+1}): {e}")
            continue
        raw_text = _get_lm_content(resp)
        parsed = _try_parse_verdict_json(raw_text)
        if parsed is not None:
            return parsed
        # 解析失敗時は raw 応答の冒頭を出してデバッグ可能にする（F3）
        preview = (raw_text or "").strip().replace("\n", " ")[:200]
        print(f"  [WARN] verify JSON 解析失敗 (試行{attempt+1}/{max_retries+1}). raw先頭: {preview}")
    print("  [ERROR] verify LLM 応答を解析できませんでした（既存 verdict を保持します）")
    return None


def _extract_body_head(body: str, n: int = 40) -> str:
    """予測本文から最初の意味ある一行を抽出して n 字に切り詰める。

    優先順位:
    1. 「予測内容：」「**予測内容**：」行があればその直後
    2. なければ最初の非空・非記号行
    title が空のアイテムを verify LLM に渡すときの代替ラベルに使う。
    """
    if not body:
        return ""
    pred_re = re.compile(r"\*{0,2}予測内容\*{0,2}[:：]\s*(.+)")
    for line in body.splitlines():
        m = pred_re.search(line)
        if m and m.group(1).strip():
            return m.group(1).strip()[:n]
    for line in body.splitlines():
        s = line.strip().lstrip("-*>").strip()
        if s and not s.startswith("**影響度"):
            return s[:n]
    return body.strip()[:n]


def _item_key(item) -> str:
    """予測アイテムの安定識別子。title 空アイテムも carry-over できるよう、
    title があればそれ、なければ本文先頭40字を使う。"""
    if getattr(item, "title", "") and item.title.strip():
        return item.title.strip()
    return _extract_body_head(getattr(item, "body", ""), 40)


def verify_horizon(report_date: str, horizon: str, items: list,
                   current_digest: str, prev_verdicts: list | None = None
                   ) -> tuple[list, float | None, int]:
    """1つの時間軸を検証する。

    prev_verdicts がある場合、「未確定」だった項目のみ再検証し、
    確定済み（的中/外れ）の項目は前回結果を引き継ぐ。

    Returns:
        (verdict_list, accuracy_score, undetermined_count)
    """
    # 再検証: 前回未確定の項目のみ抽出
    carried_over = []
    items_to_verify = items
    if prev_verdicts:
        # title 空アイテムも carry-over できるよう、key は title or body先頭40字
        prev_by_key = {v.get("title", ""): v for v in prev_verdicts}
        items_to_verify = []
        for item in items:
            key = _item_key(item)
            prev = prev_by_key.get(key) or prev_by_key.get(item.title)
            if prev and prev.get("verdict") in ("的中", "外れ"):
                carried_over.append(prev)
            else:
                items_to_verify.append(item)

    # 全て確定済みなら LLM 呼び出し不要
    new_verdicts = []
    if items_to_verify:
        # title が空の item は body 先頭から擬似ラベルを作って LLM に渡す
        # （これをしないと verify LLM が `- : 本文...` を見て解析放棄するため verdict が空になる）
        pred_lines = []
        for item in items_to_verify:
            label = item.title.strip() if item.title and item.title.strip() else _extract_body_head(item.body)
            pred_lines.append(f"- {label}: {item.body[:200]}")
        predictions_text = "\n".join(pred_lines)

        user = VERIFY_USER_TMPL.format(
            report_date=report_date,
            horizon=horizon,
            predictions_text=predictions_text,
            current_digest=current_digest,
        )
        new_verdicts_raw = _call_verify_llm(VERIFY_SYSTEM, user)
        # None は「解析失敗」を意味する。空配列で上書きせず、前ラウンドの未確定をそのまま carry over する。
        if new_verdicts_raw is None:
            print(f"  [WARN] {horizon}: verify LLM 応答不可。今回分はスキップして既存判定を維持")
            # carry_over した既存確定分のみ返し、未確定数は元 prev_verdicts から計算
            prev_undetermined = sum(
                1 for v in (prev_verdicts or []) if v.get("accuracy") is None
            ) if prev_verdicts else len(items_to_verify)
            scores_co = [float(v["accuracy"]) for v in carried_over if v.get("accuracy") is not None]
            acc_co = sum(scores_co) / len(scores_co) if scores_co else None
            return carried_over, acc_co, prev_undetermined
        new_verdicts = new_verdicts_raw
        if isinstance(new_verdicts, dict):
            new_verdicts = [new_verdicts]

    # 前回確定分と今回の結果を統合
    all_verdicts = carried_over + new_verdicts

    # スコア計算
    scores = []
    undetermined = 0
    for v in all_verdicts:
        acc = v.get("accuracy")
        if acc is not None:
            scores.append(float(acc))
        else:
            undetermined += 1

    accuracy = sum(scores) / len(scores) if scores else None
    return all_verdicts, accuracy, undetermined


def _find_verification_targets(cur, today: datetime) -> list[dict]:
    """検証すべき (report_date, horizon, round) の一覧を返す。

    判定:
    - 経過日数が HORIZON_VERIFY_SCHEDULE を満たす
    - そのラウンドが未実行
        - もしくは「verdict_json が空配列 ([]) かつ accuracy_score が None」のような
          過去のリトライ失敗による破損レコードである（リカバリー対象）
    - ラウンド2以降は前ラウンドに未確定がある
    """
    cur.execute("SELECT report_date, file_path FROM forecast_reports ORDER BY report_date DESC")
    reports = cur.fetchall()

    targets = []
    for report_date, file_path in reports:
        rd = datetime.strptime(report_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        elapsed_days = (today - rd).days

        for horizon, schedule in HORIZON_VERIFY_SCHEDULE.items():
            for required_days, round_num in schedule:
                if elapsed_days < required_days:
                    continue

                # このラウンドが既に検証済みか確認。空配列で保存された破損レコードは再検証対象。
                cur.execute("""
                    SELECT verdict_json, accuracy_score
                    FROM forecast_verifications
                    WHERE report_date = ? AND horizon = ? AND verification_round = ?
                """, (report_date, horizon, round_num))
                row = cur.fetchone()
                if row:
                    verdict_json, accuracy_score = row
                    is_broken = (
                        accuracy_score is None
                        and verdict_json is not None
                        and verdict_json.strip() in ("[]", "")
                    )
                    if not is_broken:
                        continue
                    # 破損レコードは削除して再検証可能にする（次の INSERT で改めて入る）
                    cur.execute("""
                        DELETE FROM forecast_verifications
                        WHERE report_date = ? AND horizon = ? AND verification_round = ?
                    """, (report_date, horizon, round_num))

                # ラウンド2以降は前ラウンドに未確定があるときのみ
                if round_num > 1:
                    cur.execute("""
                        SELECT undetermined_count FROM forecast_verifications
                        WHERE report_date = ? AND horizon = ? AND verification_round = ?
                    """, (report_date, horizon, round_num - 1))
                    prev_row = cur.fetchone()
                    if not prev_row or prev_row[0] == 0:
                        continue

                targets.append({
                    "report_date": report_date,
                    "file_path": file_path,
                    "horizon": horizon,
                    "round": round_num,
                })

    return targets


def _get_prev_verdicts(cur, report_date: str, horizon: str, round_num: int) -> list | None:
    """前ラウンドの判定結果を取得"""
    if round_num <= 1:
        return None
    cur.execute("""
        SELECT verdict_json FROM forecast_verifications
        WHERE report_date = ? AND horizon = ? AND verification_round = ?
    """, (report_date, horizon, round_num - 1))
    row = cur.fetchone()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            pass
    return None


def _update_report_accuracy(cur, report_date: str):
    """forecast_verifications から集約して forecast_reports.accuracy_score を更新"""
    # 各時間軸の最新ラウンドのスコアを取得
    cur.execute("""
        SELECT fv.accuracy_score, fv.verified_at
        FROM forecast_verifications fv
        INNER JOIN (
            SELECT report_date, horizon, MAX(verification_round) AS max_round
            FROM forecast_verifications
            WHERE report_date = ?
            GROUP BY report_date, horizon
        ) latest
        ON fv.report_date = latest.report_date
           AND fv.horizon = latest.horizon
           AND fv.verification_round = latest.max_round
    """, (report_date,))
    rows = cur.fetchall()

    if not rows:
        return

    scores = [r[0] for r in rows if r[0] is not None]
    max_verified = max((r[1] for r in rows if r[1]), default=None)

    avg_score = sum(scores) / len(scores) if scores else None
    cur.execute("""
        UPDATE forecast_reports
        SET accuracy_score = ?, verified_at = ?
        WHERE report_date = ?
    """, (avg_score, max_verified, report_date))


def _build_checked_markdown(report_date: str, horizon_results: dict) -> str:
    """全時間軸の検証結果をMarkdownにまとめる"""
    lines = []
    lines.append("# 検証済み予測")
    lines.append("")
    lines.append(f"**検証日時:** {datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**対象レポート:** {report_date}")
    lines.append("")

    for horizon, verdicts in horizon_results.items():
        lines.append(f"## {horizon}")
        lines.append("")
        for v in verdicts:
            title = v.get("title", "不明")
            verdict = v.get("verdict", "未確定")
            reason = v.get("reason", "")
            accuracy = v.get("accuracy")

            icon = {"的中": "✅", "外れ": "❌", "未確定": "⏳"}.get(verdict, "⏳")
            lines.append(f"### {icon} {title}")
            lines.append(f"**判定: {verdict}**")
            if accuracy is not None:
                lines.append(f"**的中度: {accuracy}**")
            lines.append(f"- {reason}")
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="過去の未来予測を検証する")
    parser.add_argument("--hours", type=int, default=0,
                        help="ニュースダイジェスト期間を上書き（0=時間軸ごとの自動設定）")
    parser.add_argument("--limit", type=int, default=10,
                        help="検証対象の最大件数（デフォルト: 10）")
    args = parser.parse_args()

    t0 = time.time()
    print("[forecast_verify] 開始")

    init_db()
    conn = connect()
    cur = conn.cursor()

    today = datetime.now(timezone.utc)
    targets = _find_verification_targets(cur, today)

    if not targets:
        print("  検証対象のレポートがありません")
        conn.close()
        return

    targets = targets[:args.limit]
    print(f"  {len(targets)}件の検証対象を検出")

    # レポートごとに整理
    reports_map = {}
    for t in targets:
        rd = t["report_date"]
        if rd not in reports_map:
            reports_map[rd] = {"file_path": t["file_path"], "horizons": []}
        reports_map[rd]["horizons"].append(t)

    # ニュースダイジェストをキャッシュ（同じ hours は使い回す）
    digest_cache = {}

    for report_date, info in reports_map.items():
        print(f"\n  レポート: {report_date}")
        md_path = Path(info["file_path"])
        if not md_path.exists():
            print(f"  [WARN] ファイルが見つかりません: {info['file_path']}")
            continue

        md_text = md_path.read_text(encoding="utf-8")
        report = parse_forecast_markdown(md_text)

        # 既存の全検証結果を集めてMarkdown再構築に使う
        horizon_results = {}

        # まず既存検証結果を読み込む
        cur.execute("""
            SELECT horizon, verdict_json, verification_round
            FROM forecast_verifications
            WHERE report_date = ?
            ORDER BY horizon, verification_round
        """, (report_date,))
        for row in cur.fetchall():
            h, vj, _ = row
            if vj:
                try:
                    horizon_results[h] = json.loads(vj)
                except json.JSONDecodeError:
                    pass

        for target in info["horizons"]:
            horizon = target["horizon"]
            round_num = target["round"]

            md_section = report.predictions.get(horizon)
            if not md_section:
                print(f"    {horizon}: 予測セクションなし、スキップ")
                continue

            items = parse_prediction_items(md_section)
            if not items:
                print(f"    {horizon}: 予測アイテムなし、スキップ")
                continue

            # ニュースダイジェスト取得
            hours = args.hours if args.hours > 0 else DIGEST_HOURS.get(horizon, 72)
            if hours not in digest_cache:
                print(f"    ニュースダイジェスト構築 ({hours}時間)...")
                digest_cache[hours] = build_news_digest(cur, hours=hours, per_cat=8)
            current_digest = digest_cache[hours]

            # 前ラウンドの結果を取得（再検証用）
            prev_verdicts = _get_prev_verdicts(cur, report_date, horizon, round_num)

            print(f"    {horizon} (ラウンド{round_num}): 検証中...")
            verdicts, accuracy, undetermined = verify_horizon(
                report_date, horizon, items, current_digest, prev_verdicts
            )

            # 検証結果が空配列で、かつ既に同 horizon に意味のある verdict が DB にある場合は
            # 上書きせずスキップ（リトライ後の暴発でデータを破壊しない）
            existing_meaningful = horizon_results.get(horizon)
            if not verdicts and existing_meaningful:
                print(f"    {horizon}: 今回は判定 0 件のため既存 verdict を保持してスキップ")
                continue

            # 検証結果をDBに保存
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cur.execute("""
                INSERT INTO forecast_verifications
                    (report_date, horizon, verification_round, verdict_json,
                     accuracy_score, undetermined_count, verified_at, digest_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_date, horizon, verification_round) DO UPDATE SET
                    verdict_json = excluded.verdict_json,
                    accuracy_score = excluded.accuracy_score,
                    undetermined_count = excluded.undetermined_count,
                    verified_at = excluded.verified_at,
                    digest_hours = excluded.digest_hours
            """, (report_date, horizon, round_num, json.dumps(verdicts, ensure_ascii=False),
                  accuracy, undetermined, now_iso, hours))

            horizon_results[horizon] = verdicts

            acc_str = f"{accuracy:.2f}" if accuracy is not None else "N/A"
            print(f"    完了: accuracy={acc_str}, 未確定={undetermined}件")

        # forecast_reports の集約スコアを更新
        _update_report_accuracy(cur, report_date)
        conn.commit()

        # Markdownの検証セクションを更新
        if horizon_results:
            checked_md = _build_checked_markdown(report_date, horizon_results)
            if "# 検証済み予測" not in md_text:
                updated_md = md_text.rstrip() + "\n\n---\n\n" + checked_md
            else:
                idx = md_text.index("# 検証済み予測")
                updated_md = md_text[:idx].rstrip() + "\n\n" + checked_md
            md_path.write_text(updated_md, encoding="utf-8")

    conn.close()
    elapsed = time.time() - t0
    print(f"\n[forecast_verify] 完了 ({elapsed:.1f}秒)")


if __name__ == "__main__":
    main()
