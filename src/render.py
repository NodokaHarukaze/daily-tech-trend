from pathlib import Path
from jinja2 import Template
import yaml
from db import connect

HTML = r"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Daily Tech Trend</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;line-height:1.5}
    h1{margin:0 0 12px}
    h2{margin:28px 0 8px;border-bottom:1px solid #eee;padding-bottom:6px}
    .meta{color:#666;font-size:12px;margin:6px 0 14px}
    ul{margin:0;padding-left:18px}
    li{margin:6px 0}
    .tag{display:inline-block;border:1px solid #ddd;border-radius:999px;padding:2px 8px;font-size:12px;color:#444;margin-left:8px}
    .badge{display:inline-block;border:1px solid #ccc;border-radius:6px;padding:1px 6px;font-size:12px;margin-left:6px}
    .topbox{background:#fafafa;border:1px solid #eee;border-radius:10px;padding:10px 12px;margin:10px 0 14px}
    .topbox h3{margin:0 0 6px;font-size:14px}
    .small{color:#666;font-size:12px}
  </style>
</head>
<body>
  <h1>Daily Tech Trend</h1>
  <div class="meta">カテゴリ別（最新テーマ）＋ 注目TOP5（続報多い順）</div>

  {% for cat in categories %}
    <h2>{{ cat.name }} <span class="tag">{{ cat.id }}</span></h2>

    <div class="topbox">
      <h3>注目TOP5（続報多い順）</h3>
      {% if hot_by_cat.get(cat.id) %}
        <ul>
          {% for item in hot_by_cat[cat.id] %}
            <li>
              {{ item.title }}
              <span class="badge">続報 {{ item.followups }}</span>
              <span class="small">（記事 {{ item.articles }}）</span>
            </li>
          {% endfor %}
        </ul>
      {% else %}
        <div class="small">該当なし</div>
      {% endif %}
    </div>

    {% if topics_by_cat.get(cat.id) %}
      <ul>
        {% for t in topics_by_cat[cat.id] %}
          <li>{{ t }}</li>
        {% endfor %}
      </ul>
    {% else %}
      <div class="meta">該当なし</div>
    {% endif %}
  {% endfor %}
</body>
</html>
"""

def load_categories():
    with open("src/sources.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("categories", [])

def main():
    conn = connect()
    cur = conn.cursor()

    categories = load_categories()
    topics_by_cat = {}
    hot_by_cat = {}

    LIMIT_PER_CAT = 20
    HOT_TOP_N = 5

    for cat in categories:
        cat_id = cat["id"]

        # 最新テーマ
        cur.execute(
            """
            SELECT COALESCE(title_ja, title)
            FROM topics
            WHERE category = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (cat_id, LIMIT_PER_CAT)
        )
        topics_by_cat[cat_id] = [r[0] for r in cur.fetchall()]

        # 注目TOP5（続報多い順 = topic内の記事数）
        cur.execute(
            """
            SELECT
              t.id,
              COALESCE(t.title_ja, t.title) AS ttitle,
              COUNT(ta.article_id) AS article_count,
              MAX(t.created_at) AS created_at
            FROM topics t
            JOIN topic_articles ta ON ta.topic_id = t.id
            WHERE t.category = ?
            GROUP BY t.id
            ORDER BY article_count DESC, created_at DESC, t.id DESC
            LIMIT ?
            """,
            (cat_id, HOT_TOP_N)
        )
        rows = cur.fetchall()
        hot_by_cat[cat_id] = [
            {
                "id": tid,
                "title": title,
                "articles": int(ac),
                "followups": max(int(ac) - 1, 0),
            }
            for (tid, title, ac, _created_at) in rows
        ]

    conn.close()

    out_dir = Path("docs")
    out_dir.mkdir(exist_ok=True)
    (out_dir / "index.html").write_text(
        Template(HTML).render(
            categories=categories,
            topics_by_cat=topics_by_cat,
            hot_by_cat=hot_by_cat
        ),
        encoding="utf-8"
    )

if __name__ == "__main__":
    main()
