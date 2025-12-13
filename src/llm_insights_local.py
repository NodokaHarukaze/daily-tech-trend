import json, sqlite3, hashlib
from datetime import datetime, timezone
import requests

LMSTUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"  # 必要なら変更
MODEL = "local-model"  # LM Studio側で指定不要なら任意文字列でOK

def connect():
    return sqlite3.connect("data/state.sqlite")

def _now():
    return datetime.now(timezone.utc).isoformat()

def pick_topic_inputs(conn, limit=30):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # topic代表記事（最新fetched_at）を1件引く
    cur.execute("""
      SELECT
        t.id AS topic_id,
        COALESCE(t.title_ja, t.title) AS topic_title,
        t.category AS category,
        a.url AS url,
        COALESCE(a.content, a.summary, a.title, '') AS body
      FROM topics t
      JOIN topic_articles ta ON ta.topic_id = t.id
      JOIN articles a ON a.id = ta.article_id
      WHERE a.id = (
        SELECT a2.id
        FROM topic_articles ta2
        JOIN articles a2 ON a2.id = ta2.article_id
        WHERE ta2.topic_id = t.id
        ORDER BY datetime(a2.fetched_at) DESC, a2.id DESC
        LIMIT 1
      )
      ORDER BY t.id DESC
      LIMIT ?
    """, (limit,))
    return cur.fetchall()

def call_llm(topic_title, category, url, body):
    # 入力を短く（コスト0でも速度のため）
    body = (body or "").strip()
    body = body[:2000]

    system = (
        "あなたはシステム開発者向けの技術トレンド分析アシスタント。"
        "推測は必ず『推測』と明示し、根拠URLを添える。出力は必ずJSONのみ。"
    )
    user = {
        "topic_title": topic_title,
        "category": category,
        "evidence_url": url,
        "text": body
    }

    schema = {
        "importance": "0-100の整数",
        "type": "security|release|research|incident|biz|other",
        "summary": "日本語120字以内の要約1行",
        "key_points": ["要点を3つ（短文）"],
        "impact_guess": "推測を含む影響/示唆（推測は推測と明記）",
        "next_actions": ["次アクションを3つ（確認/対応/提案）"],
        "evidence_urls": ["根拠URL（最低1つ）"]
    }

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "次の入力を分析し、指定スキーマでJSONのみ返してください。"},
            {"role": "user", "content": f"スキーマ: {json.dumps(schema, ensure_ascii=False)}"},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)}
        ],
        "temperature": 0.2,
        "max_tokens": 500
    }

    r = requests.post(LMSTUDIO_URL, json=payload, timeout=120)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()

    # JSON以外を混ぜるモデル対策（最初の{から最後の}を抜く）
    if "{" in text and "}" in text:
        text = text[text.find("{"):text.rfind("}")+1]
    return json.loads(text)

def upsert_insight(conn, topic_id, insight):
    cur = conn.cursor()
    cur.execute("""
      INSERT INTO topic_insights(topic_id, importance, type, summary, key_points,
                                impact_guess, next_actions, evidence_urls, updated_at)
      VALUES(?,?,?,?,?,?,?,?,?)
      ON CONFLICT(topic_id) DO UPDATE SET
        importance=excluded.importance,
        type=excluded.type,
        summary=excluded.summary,
        key_points=excluded.key_points,
        impact_guess=excluded.impact_guess,
        next_actions=excluded.next_actions,
        evidence_urls=excluded.evidence_urls,
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
        _now()
    ))

def main():
    conn = connect()

    rows = pick_topic_inputs(conn, limit=30)  # まずは30件で運用開始がおすすめ
    for r in rows:
        try:
            ins = call_llm(r["topic_title"], r["category"], r["url"], r["body"])
            # URLを必ず入れる保険
            if "evidence_urls" not in ins or not ins["evidence_urls"]:
                ins["evidence_urls"] = [r["url"]]
            upsert_insight(conn, r["topic_id"], ins)
            conn.commit()
        except Exception as e:
            # 失敗しても止めない
            continue

    conn.close()

if __name__ == "__main__":
    main()
