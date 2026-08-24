"""LLM 応答が壊れて定型フォールバック文言に差し替わった insight を作り直す。

2026-08-24: `call_llm_short_news` に `reasoning_effort` が無く、gpt-oss:20b が
既定の reasoning で `max_tokens` を使い切って JSON が途中で切れていた。破断時の
フォールバックは `summary` に記事タイトルを入れるため `importance>0` かつ
`summary` 非空となり、`_needs_repair()` が「健全」と誤判定して `src_hash` 一致で
永久にスキップされていた（詳細は tasks/knowledge.md）。

生成側の不具合は修正済みなので新規分は健全だが、過去に壊れた行は夜間の
`llm_insights_local.py --rescue` が新しい順に処理するため掘り起こしに時間がかかる。
このスクリプトは壊れた行だけを直接狙って作り直す。

使い方:
    python src/backfill_insight_fallback.py --dry-run              # 対象件数の確認のみ
    python src/backfill_insight_fallback.py --since 2026-07-01     # 直近2ヶ月分
    python src/backfill_insight_fallback.py --since 2026-07-01 --max-sec 3600

夜間パイプライン（06/09/12/15/18/21 時に起動しうる）と DB・LLM を取り合わないよう、
既定では毎正時から 35 分間は処理を止めて待つ（--no-window-guard で無効化）。
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_insights_api import call_llm
from llm_insights_pipeline import (
    NEWS_FALLBACK_KEY_POINT,
    compute_src_hash,
    postprocess_insight,
    upsert_insight,
)

# 夜間パイプラインが起動しうる時刻（タスクスケジューラのトリガー）。
# この時刻の 0〜PIPELINE_WINDOW_MIN 分は処理を止めて待つ。
PIPELINE_HOURS = (6, 9, 12, 15, 18, 21)
PIPELINE_WINDOW_MIN = 35


def _parse_args(argv):
    p = argparse.ArgumentParser(description="Rebuild insights that fell back to boilerplate")
    p.add_argument("--since", default="2026-07-01",
                   help="この日付以降に生成された行のみ対象にする (既定: 2026-07-01、'all' で全期間)")
    p.add_argument("--limit", type=int, default=5000, help="最大処理件数 (既定: 5000)")
    p.add_argument("--max-sec", type=int, default=0,
                   help="時間予算（秒）。0 で無制限 (既定: 0)")
    p.add_argument("--delay", type=float, default=0.0, help="1件ごとの待機秒 (既定: 0)")
    p.add_argument("--dry-run", action="store_true", help="対象件数だけ表示して終了する")
    p.add_argument("--no-window-guard", action="store_true",
                   help="夜間パイプライン時間帯の待避を無効にする")
    return p.parse_args(argv)


def _wait_out_pipeline_window(enabled: bool) -> None:
    """夜間パイプラインの実行時間帯なら、抜けるまで待つ。"""
    if not enabled:
        return
    while True:
        now = datetime.now()
        if now.hour in PIPELINE_HOURS and now.minute < PIPELINE_WINDOW_MIN:
            wait = (PIPELINE_WINDOW_MIN - now.minute) * 60 - now.second
            print(f"[WAIT] 夜間パイプライン時間帯のため {wait}秒 待機 ({now:%H:%M:%S})", flush=True)
            time.sleep(max(1, wait))
            continue
        return


def pick_fallback_rows(conn, since: str, limit: int):
    """フォールバック文言のまま残っている insight を、代表記事つきで新しい順に返す。

    title / category の決め方は `pick_topic_inputs` と同じにする。ここがずれると
    `compute_src_hash` の値も LLM のプロンプト分岐（news / tech）も変わってしまい、
    次回の `--rescue` が「内容が変わった」と誤認して再生成を繰り返す。
    """
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    since_clause = "" if since == "all" else "AND datetime(i.updated_at) >= datetime(?)"
    params = [f"%{NEWS_FALLBACK_KEY_POINT}%"]
    if since != "all":
        params.append(since)
    params.append(limit)
    cur.execute(
        f"""
        WITH latest AS (
          SELECT ta.topic_id, a.id AS article_id, a.kind, a.url,
                 a.title, a.title_ja, a.category AS article_category,
                 COALESCE(NULLIF(a.content,''), NULLIF(a.title_ja,''), NULLIF(a.title,''), '') AS body,
                 ROW_NUMBER() OVER (
                   PARTITION BY ta.topic_id
                   ORDER BY COALESCE(a.published_at, a.fetched_at) DESC, a.id DESC
                 ) AS rn
          FROM topic_articles ta JOIN articles a ON a.id = ta.article_id
        )
        SELECT i.topic_id,
               l.article_id,
               CASE
                 WHEN COALESCE(NULLIF(t.category,''),'') = 'news'
                   THEN COALESCE(NULLIF(l.title_ja,''), NULLIF(l.title,''), NULLIF(t.title_ja,''), NULLIF(t.title,''))
                 ELSE COALESCE(NULLIF(t.title_ja,''), NULLIF(t.title,''), NULLIF(l.title_ja,''), NULLIF(l.title,''))
               END AS title,
               CASE
                 WHEN COALESCE(NULLIF(t.category,''),'') = 'news' THEN 'news'
                 ELSE COALESCE(NULLIF(l.article_category,''), NULLIF(t.category,''), 'other')
               END AS category,
               l.url, l.body, l.kind, i.updated_at
        FROM topic_insights i
        JOIN topics t ON t.id = i.topic_id
        JOIN latest l ON l.topic_id = i.topic_id AND l.rn = 1
        WHERE i.key_points LIKE ? {since_clause}
        ORDER BY datetime(i.updated_at) DESC, i.topic_id DESC
        LIMIT ?
        """,
        params,
    )
    return cur.fetchall()


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    t0 = time.time()

    base = Path(__file__).resolve().parent.parent
    conn = sqlite3.connect(base / "data" / "state.sqlite", timeout=120)
    try:
        rows = pick_fallback_rows(conn, args.since, args.limit)
        print(f"[INFO] 対象 {len(rows)} 件 (since={args.since}, limit={args.limit})", flush=True)
        if args.dry_run:
            for r in rows[:5]:
                print(f"  topic={r['topic_id']} updated={r['updated_at']} {r['title'][:40]}")
            return 0

        ok = ng = 0
        for i, r in enumerate(rows, 1):
            if args.max_sec and (time.time() - t0) >= args.max_sec:
                print(f"[TIME] 時間予算に到達 sec={time.time()-t0:.0f}", flush=True)
                break
            _wait_out_pipeline_window(not args.no_window_guard)
            t1 = time.time()
            try:
                raw = call_llm(r["title"], r["category"], r["url"], r["body"], kind=r["kind"])
                ins = postprocess_insight(raw, r)
                upsert_insight(conn, r["topic_id"], ins, r["article_id"],
                               compute_src_hash(r["title"], r["url"], r["body"]))
                conn.commit()
                ok += 1
                print(f"[{i}/{len(rows)}] OK topic={r['topic_id']} imp={ins['importance']} "
                      f"{time.time()-t1:.1f}s :: {ins['summary'][:40]}", flush=True)
            except sqlite3.Error:
                # DB 異常は全件で再発するので握りつぶさない
                raise
            except Exception as e:
                ng += 1
                print(f"[{i}/{len(rows)}] NG topic={r['topic_id']} err={e}", flush=True)
            if args.delay > 0:
                time.sleep(args.delay)
        print(f"[DONE] ok={ok} ng={ng} sec={time.time()-t0:.0f}", flush=True)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
