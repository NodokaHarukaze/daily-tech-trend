# src/render.py

import re
import sqlite3
from pathlib import Path
from jinja2 import Template

HTML = """<!doctype html>
<meta charset="utf-8"/>
<title>Daily Tech Trend</title>
<h1>Daily Tech Trend</h1>

{% for cat in categories %}
  <h2>{{ cat.name }}</h2>

  <h3>注目TOP5（続報多い順）</h3>
  <ol>
  {% for t in cat.top5 %}
    <li>
      <a href="{{ t.url }}" target="_blank" rel="noopener">{{ t.title }}</a><br/>
      <small>{{ t.summary }}</small>
      <div><small>score(48h): {{ t.score }}</small></div>
    </li>
  {% endfor %}
  </ol>

  <h3>一覧</h3>
  <ul>
  {% for t in cat.items %}
    <li>
      <a href="{{ t.url }}" target="_blank" rel="noopener">{{ t.title }}</a><br/>
      <small>{{ t.summary }}</small>
      <div><small>score(48h): {{ t.score }}</small></div>
    </li>
  {% endfor %}
  </ul>
{% endfor %}
"""

def connect():
  return sqlite3.connect("data/state.sqlite")

def one_line_summary(text: str, fallback: str = "", max_len: int = 120) -> str:
  if not text:
    return fallback[:max_len]

  s = re.sub(r"\s+", " ", text).strip()
  # 先頭1文っぽく切る（日本語/英語ざっくり）
  m = re.split(r"(?:。|！|？|\. |\.\n)", s, maxsplit=1)
  first = m[0].strip() if m else s

  if len(first) < 10:  # 短すぎる時は少し伸ばす
    first = s[:max_len]
  return first[:max_len]

def main():
  conn = connect()
  conn.row_factory = sqlite3.Row
  cur = conn.cursor()

  # 直近48hスコア（例）：topic_threads/mentions がある前提
  # ※あなたの実DBに合わせて「48h増分」を算出するSQLへ置き換えてOK
  # ここでは topics.score_48h が既に計算済み、という形でもOK
  cur.execute("""
    SELECT
      t.id AS topic_id,
      COALESCE(t.title_ja, t.title) AS title,
      t.category AS category,
      t.score_48h AS score,             -- ←「直近48hに増えた分」スコアをここに
      a.url AS url,
      COALESCE(a.content, a.summary, '') AS body
    FROM topics t
    LEFT JOIN topic_articles ta ON ta.topic_id = t.id
    LEFT JOIN articles a ON a.id = ta.article_id
    -- 各topicの代表記事（最新）を取る：実装に合わせて調整
    WHERE a.id = (
      SELECT a2.id
      FROM topic_articles ta2
      JOIN articles a2 ON a2.id = ta2.article_id
      WHERE ta2.topic_id = t.id
      ORDER BY a2.published_at DESC
      LIMIT 1
    )
    ORDER BY t.category, t.score_48h DESC
  """)

  rows = cur.fetchall()

  # category単位に整形
  by_cat = {}
  for r in rows:
    cat = r["category"] or "その他"
    by_cat.setdefault(cat, [])
    by_cat[cat].append({
      "title": r["title"],
      "summary": one_line_summary(r["body"], fallback=r["title"]),
      "url": r["url"] or "#",
      "score": int(r["score"] or 0),
    })

  categories = []
  for cat_name, items in by_cat.items():
    items_sorted = sorted(items, key=lambda x: x["score"], reverse=True)
    categories.append({
      "name": cat_name,
      "top5": items_sorted[:5],
      "items": items_sorted,
    })

  site = Path("docs")
  site.mkdir(exist_ok=True)
  (site / "index.html").write_text(
    Template(HTML).render(categories=categories),
    encoding="utf-8"
  )

if __name__ == "__main__":
  main()
# ===== DB 初期化（これが無かった） =====

def init_db():
    conn = connect()
    cur = conn.cursor()

    # articles
    cur.execute("""
    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE,
        title TEXT,
        title_ja TEXT,
        content TEXT,
        category TEXT,
        published_at TEXT,
        fetched_at TEXT
    )
    """)

    # topics
    cur.execute("""
    CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_key TEXT UNIQUE,
        title TEXT,
        title_ja TEXT,
        category TEXT,
        created_at TEXT
    )
    """)

    # topic_articles
    cur.execute("""
    CREATE TABLE IF NOT EXISTS topic_articles (
        topic_id INTEGER,
        article_id INTEGER,
        UNIQUE(topic_id, article_id)
    )
    """)

    # edges（続報ツリー）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS edges (
        topic_id INTEGER,
        parent_article_id INTEGER,
        child_article_id INTEGER,
        UNIQUE(topic_id, parent_article_id, child_article_id)
    )
    """)

    conn.commit()
    conn.close()
