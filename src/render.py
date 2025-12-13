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
  <div class="meta">カテゴリ別（最新テーマ）＋ 注目TOP5（48h増分）</div>

  {% for cat in categories %}
    <h2>{{ cat.name }} <span class="tag">{{ cat.id }}</span></h2>

    <div class="topbox">
      <h3>注目TOP5（48h増分）</h3>
      {% if hot_by_cat.get(cat.id) %}
        <ul>
          {% for item in hot_by_cat[cat.id] %}
            <li>
              {{ item.title }}
              <span class="badge">48h +{{ item.recent }}</span>
              <span class="small">（累計 {{ item.articles }}）</span>
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

def load_categories_from_yaml():
    # sources.yaml が無い / categories が無い場合でも落とさない
    try:
        with open("src/sources.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cats = cfg.get("categories")
        if isinstance(cats, list) and all(("id" in c and "name" in c) for c in cats):
            return cats
    except Exception:
        pass
    return []

def build_categories_fallback(cur):
    """
    YAMLにカテゴリが無い/不完全な場合:
    DBの topics.category / articles.category からカテゴリ集合を作り、
    それでも無ければ 'other' を用意する。
    """
    # topicsからカテゴリ収集
    cur.execute("SELECT DISTINCT category FROM topics WHERE category IS NOT NULL AND category != ''")
    cats = [r[0] for r in cur.fetchall()]

    # topicsが空なら articlesからも拾う
    if not cats:
        cur.execute("SELECT DISTINCT category FROM articles WHERE category IS NOT NULL AND category != ''")
        cats = [r[0] for r in cur.fetchall()]

    # 何も無ければ other
    if not cats:
        cats = ["other"]

    # 表示名は簡易変換（必要なら増やす）
    name_map = {
        "system": "システム",
        "manufacturing": "製造",
        "security": "セキュリティ",
        "ai_data": "AI/データ",
        "dev": "開発",
        "other": "その他",
    }
    return [{"id": c, "name": name_map.get(c, c)} for c in cats]

def ensure_category_coverage(cur, categories):
    """
    categories に無いカテゴリがDB側にある場合、末尾に追加して表示対象にする。
    """
    ids = {c["id"] for c in categories}

    cur.execute("SELECT DISTINCT category FROM topics WHERE category IS NOT NULL AND category != ''")
    db_cats = [r[0] for r in cur.fetchall()]

    name_map = {
        "system": "システム",
        "manufacturing": "製造",
        "security": "セキュリティ",
        "ai_data": "AI/データ",
        "dev": "開発",
        "other": "その他",
    }

    for c in db_cats:
        if c not in ids:
            categories.append({"id": c, "name": name_map.get(c, c)})
            ids.add(c)

    # まだ空なら other
    if not categories:
        categories.append({"id": "other", "name": "その他"})

    return categories

def main():
    conn = connect()
    cur = conn.cursor()

    # 1) categories を YAML から試す
    categories = load_categories_from_yaml()

    # 2) YAMLが無い/空ならDBから作る
    if not categories:
        categories = build_categories_fallback(cur)

    # 3) YAMLに無いカテゴリもDBから拾って追加（空表示防止）
    categories = ensure_category_coverage(cur, categories)

    topics_by_cat = {}
    hot_by_cat = {}

    LIMIT_PER_CAT = 20
    HOT_TOP_N = 5

    for cat in categories:
        cat_id = cat["id"]

        # 最新テーマ（カテゴリがNULL/空の場合は other に寄せる）
        if cat_id == "other":
            cur.execute(
                """
                SELECT COALESCE(title_ja, title)
                FROM topics
                WHERE category IS NULL OR category = ''
                ORDER BY id DESC
                LIMIT ?
                """,
                (LIMIT_PER_CAT,)
            )
        else:
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

        # 注目TOP5：48h増分（fetched_atベース）
        if cat_id == "other":
            cur.execute(
                """
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS ttitle,
                  COUNT(ta.article_id) AS total_count,
                  SUM(
                    CASE
                      WHEN datetime(a.fetched_at) >= datetime('now', '-48 hours') THEN 1
                      ELSE 0
                    END
                  ) AS recent_count
                FROM topics t
                JOIN topic_articles ta ON ta.topic_id = t.id
                JOIN articles a ON a.id = ta.article_id
                WHERE t.category IS NULL OR t.category = ''
                GROUP BY t.id
                HAVING recent_count > 0
                ORDER BY recent_count DESC, total_count DESC, t.id DESC
                LIMIT ?
                """,
                (HOT_TOP_N,)
            )
        else:
            cur.execute(
                """
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS ttitle,
                  COUNT(ta.article_id) AS total_count,
                  SUM(
                    CASE
                      WHEN datetime(a.fetched_at) >= datetime('now', '-48 hours') THEN 1
                      ELSE 0
                    END
                  ) AS recent_count
                FROM topics t
                JOIN topic_articles ta ON ta.topic_id = t.id
                JOIN articles a ON a.id = ta.article_id
                WHERE t.category = ?
                GROUP BY t.id
                HAVING recent_count > 0
                ORDER BY recent_count DESC, total_count DESC, t.id DESC
                LIMIT ?
                """,
                (cat_id, HOT_TOP_N)
            )

        rows = cur.fetchall()
        hot_by_cat[cat_id] = [
            {
                "id": tid,
                "title": title,
                "articles": int(total),
                "recent": int(recent),
            }
            for (tid, title, total, recent) in rows
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
