import json, sqlite3, hashlib
from datetime import datetime, timezone
import requests
from datetime import timedelta
import re
LMSTUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"  # 必要なら変更
MODEL = "local-model"  # LM Studio側で指定不要なら任意文字列でOK
import time

def _now_sec():
    return time.perf_counter()

def has_insight(cur, topic_id: int) -> bool:
    cur.execute(
        "SELECT 1 FROM topic_insights WHERE topic_id = ? LIMIT 1",
        (topic_id,),
    )
    return cur.fetchone() is not None

def connect():
    return sqlite3.connect("data/state.sqlite")

def _now():
    return datetime.now(timezone.utc).isoformat()

def pick_topic_inputs(conn, limit=30):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cutoff_48h = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")

    cur.execute("""
      SELECT
        t.id AS topic_id,
        COALESCE(t.title_ja, t.title) AS topic_title,
        t.category AS category,
        a.url AS url,
        COALESCE(a.content, a.title, '') AS body
      FROM topics t
      JOIN topic_articles ta ON ta.topic_id = t.id
      JOIN articles a ON a.id = ta.article_id
      LEFT JOIN topic_insights i ON i.topic_id = t.id
      WHERE (
            i.topic_id IS NULL
         OR COALESCE(i.summary,'') = ''
         OR COALESCE(i.key_points,'') = ''
         OR COALESCE(i.key_points,'') = '[]'
         OR COALESCE(i.next_actions,'') = ''
         OR COALESCE(i.next_actions,'') = '[]'
        )
        AND (
          SELECT COALESCE(SUM(
            CASE
              WHEN datetime(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at)) >= datetime(?)
              THEN 1 ELSE 0
            END
          ), 0)
          FROM topic_articles ta3
          JOIN articles a3 ON a3.id = ta3.article_id
          WHERE ta3.topic_id = t.id
        ) > 0
        AND a.id = (
          SELECT a2.id
          FROM topic_articles ta2
          JOIN articles a2 ON a2.id = ta2.article_id
          WHERE ta2.topic_id = t.id
          ORDER BY
            CASE WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0 ELSE 1 END,
            datetime(a2.fetched_at) DESC,
            datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
            a2.url ASC
          LIMIT ?
        )
    """, (cutoff_48h, limit))

    return cur.fetchall()


def call_llm(topic_title, category, url, body):
    # 入力を短く（コスト0でも速度のため）
    body = (body or "").strip()
    body = body[:1200]

    system = (
      "あなたは技術判断を行うエンジニア/企画担当向けのトレンド分析アシスタント。"
      "一般向け説明、感想、前置き、結論以外の余談は禁止。"
      "出力はJSONのみ。JSON以外の文字（挨拶、説明、コードブロック、注釈）を一切出さない。"
      "必ず全フィールドを出力し、欠落や型違いは禁止。"
      "key_pointsは入力textに明記された事実のみ。推測・解釈・一般論は禁止。"
      "impact_guessは推測可だが、推測が含まれる文は必ず文頭に『推測：』。"
      "next_actionsは実行可能なタスクに限定し、actionは命令形、expected_outcomeは得られる成果を明示。"
      "evidence_urlsは必ず1つ以上で、入力のevidence_urlを必ず含める。"
      "tagsは短い名詞、重複禁止、最大5"
      "出力は必ずJSONオブジェクト1つのみ。"
      "前後に文章や注釈、Markdown、コードフェンスは禁止。"
      "必須キー: importance(int), summary(string)"
    )


    user = {
    
        "topic_title": topic_title,
        "category": category,
        "evidence_url": url,
        "text": body
    }
    
    schema = {
    "importance": (
        "0-100の整数。以下の基準で評価する。\n"
        "0-30: 周辺情報・補足的ニュース（今すぐの実務影響は小さい）\n"
        "31-60: 実務影響あり（設計・実装・運用に影響する可能性）\n"
        "61-80: 業界影響（技術トレンドや標準、競争環境に影響）\n"
        "81-100: パラダイム変化（前提や常識を変える可能性が高い）\n"
        "評価では必ず次を考慮する：\n"
        "- 既存システムの修正・検証が必要か\n"
        "- 6ヶ月以内に対応判断が必要か\n"
        "- 特定ベンダー依存か／業界全体か"
    ),
    "type": "security|release|research|incident|biz|other",
    "summary": "日本語180字以内の要約1行（結論→理由の順）",
    "key_points": [
      "本文に明記された事実を3つ（各15〜40字、推測禁止）"
    ],
    "impact_guess": (
      "影響・示唆。必要なら推測を含めてよいが、その場合は必ず文頭に『推測：』。"
      "エンジニア視点/事業視点をそれぞれ1文ずつ（合計2文）"
    ),
    
    "next_actions": [
      {
        "action": "次にやる具体アクション（命令形、25〜60字）",
        "expected_outcome": "その結果得られるもの（名詞句中心、20〜60字）",
        "priority": "now|next|later"
      }
    ],

    "perspectives": {
      "sales": "営業視点コメント（40〜90字）",
      "engineer": "技術者視点コメント（40〜90字）",
      "management": "経営者視点コメント（40〜90字）"
    },
    "tags": [
      "1〜5個。短い名詞。本文/要約から抽出。足りない場合は推測で補ってよい（例: EU規制, 脆弱性, 鉄鋼, 脱炭素, 生成AI）"
    ],
    "evidence_urls": ["根拠URL（最低1つ）"]
}


    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "次の入力を分析し、スキーマに厳密準拠したJSONのみを返してください。前後に説明文・コードブロックは禁止。"},
            {"role": "user", "content": f"スキーマ: {json.dumps(schema, ensure_ascii=False)}"},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            {"role": "user", "content": "出力チェック: (1) JSONのみ (2) フィールド欠落なし (3) 型一致 (4) summary<=180字 (5) key_pointsは本文の事実のみ (6) evidence_urlをevidence_urlsに含める。違反があれば自己修正してから最終JSONを出力せよ。"}
        ],
        "temperature": 0.2,
        "max_tokens": 500
    }

    r = requests.post(LMSTUDIO_URL, json=payload, timeout=120)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()

    # ... LLM応答 text を取得したあと
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        # 文章しか返ってこない場合も修復に回す
        return _repair_json_with_llm(text)

    candidate = m.group(0)
    try:
        return json.loads(candidate)
    except Exception:
        # JSONの括弧/クォート崩れを1回だけ修復
        return _repair_json_with_llm(candidate)


def _repair_json_with_llm(bad_text: str) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "次のテキストを、有効なJSONだけに修復して返せ。JSON以外は禁止。"},
            {"role": "user", "content": bad_text},
        ],
        "temperature": 0.0,
        "max_tokens": 700,
    }
    r = requests.post(LMSTUDIO_URL, json=payload, timeout=120)
    r.raise_for_status()
    fixed = r.json()["choices"][0]["message"]["content"].strip()
    m = re.search(r"\{.*\}", fixed, re.DOTALL)
    if not m:
        raise ValueError("no json object in repaired response")
    return json.loads(m.group(0))

def upsert_insight(conn, topic_id, insight):
    cur = conn.cursor()
    cur.execute("""
      INSERT INTO topic_insights(
        topic_id,
        importance,
        type,
        summary,
        key_points,
        impact_guess,
        next_actions,
        evidence_urls,
        tags,
        updated_at
      )
      VALUES (?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(topic_id) DO UPDATE SET
        importance=excluded.importance,
        type=excluded.type,
        summary=excluded.summary,
        key_points=excluded.key_points,
        impact_guess=excluded.impact_guess,
        next_actions=excluded.next_actions,
        evidence_urls=excluded.evidence_urls,
        tags=excluded.tags,
        updated_at=excluded.updated_at
    """, (
        topic_id,
        int(insight.get("importance", 0)),
        insight.get("type", "other"),
        insight.get("summary", ""),
        json.dumps(insight.get("key_points", []), ensure_ascii=False),
        insight.get("impact_guess", ""),
        json.dumps(insight.get("next_actions", []), ensure_ascii=False),
        json.dumps(insight.get("evidence_urls", []), ensure_ascii=False),
        json.dumps(insight.get("tags", []), ensure_ascii=False),  # ★ 追加
        _now()
    ))


def main():
    t0 = _now_sec()
    print("[TIME] step=llm start")
    conn = connect()
    cur = conn.cursor()

    rows = pick_topic_inputs(conn, limit=30)
    if not rows:
        print("[INFO] no topics to summarize")
        conn.close()
        return
    for r in rows:
      topic_id = r["topic_id"]

      try:
          t1 = _now_sec()
          ins = call_llm(
              r["topic_title"],
              r["category"],
              r["url"],
              r["body"]
          )
          print(f"[TIME] llm_one topic={topic_id} sec={_now_sec() - t1:.1f}")

          # URLを必ず入れる保険
          if "evidence_urls" not in ins or not ins["evidence_urls"]:
              ins["evidence_urls"] = [r["url"]]

          upsert_insight(conn, topic_id, ins)
          conn.commit()

          print(f"[OK] insight saved topic_id={topic_id} imp={ins.get('importance')} cat={r['category']}")

      except Exception as e:
          print(f"[WARN] insight failed topic_id={topic_id} cat={r['category']} url={r['url']} err={e}")
          upsert_insight(conn, topic_id, {
              "importance": 0,
              "type": "other",
              "summary": "",
              "key_points": [],
              "impact_guess": "",
              "next_actions": [],
              "evidence_urls": [r["url"]],
          })
          conn.commit()
          continue

    conn.close()

    print(f"[TIME] step=llm end sec={_now_sec() - t0:.1f}")

if __name__ == "__main__":
    main()
