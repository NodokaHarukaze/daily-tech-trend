# src/render.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import yaml
from jinja2 import Template
from datetime import datetime, timedelta, timezone

from db import connect

HTML = r"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Daily Tech Trend</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;line-height:1.55}
    h1{margin:0 0 12px}
    h2{margin:28px 0 8px;border-bottom:1px solid #eee;padding-bottom:6px}
    .meta{color:#666;font-size:12px;margin:6px 0 14px}
    .tag{display:inline-block;border:1px solid #ddd;border-radius:999px;padding:2px 8px;font-size:12px;color:#444;margin-left:8px}
    .badge{display:inline-block;border:1px solid #ccc;border-radius:6px;padding:1px 6px;font-size:12px;margin-left:6px}
    .topbox{background:#fafafa;border:1px solid #eee;border-radius:10px;padding:10px 12px;margin:10px 0 14px}
    .topbox h3{margin:0 0 6px;font-size:14px}
    ul{margin:0;padding-left:18px}
    li{margin:8px 0}
    .small{color:#666;font-size:12px}
    .insight{margin-top:6px;padding:8px 10px;border:1px solid #eee;border-radius:10px;background:#fff}
    .imp{font-weight:700}
    .kps, .nas{margin:6px 0 0 0}
    .kps li, .nas li{margin:4px 0}
    a{color:inherit}
  </style>
</head>
<body>
  <h1>Daily Tech Trend</h1>
  <div class="meta">カテゴリ別（最新テーマ）＋ 注目TOP5（48h増分）＋ LLM解説（ローカル）</div>

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
          <li>
            <div>
              {% if t.url and t.url != "#" %}
                <a href="{{ t.url }}" target="_blank" rel="noopener">{{ t.title }}</a>
              {% else %}
                {{ t.title }}
              {% endif %}

              {% if t.importance is not none %}
                <span class="badge imp">重要度 {{ t.importance }}</span>
              {% endif %}

              {% if t.recent is not none %}
                <span class="badge">48h +{{ t.recent }}</span>
              {% endif %}
            </div>

            {% if t.summary or (t.key_points and t.key_points|length>0) or t.impact_guess or (t.next_actions and t.next_actions|length>0) %}
              <div class="insight">
                {% if t.summary %}
                  <div><strong>要約</strong>：{{ t.summary }}</div>
                {% endif %}

                {% if t.key_points and t.key_points|length>0 %}
                  <ul class="kps">
                    {% for kp in t.key_points %}
                      <li>{{ kp }}</li>
                    {% endfor %}
                  </ul>
                {% endif %}

                {% if t.impact_guess %}
                  <div style="margin-top:6px;"><strong>影響・示唆（推測含む）</strong>：{{ t.impact_guess }}</div>
                {% endif %}

                {% if t.next_actions and t.next_actions|length>0 %}
                  <div style="margin-top:6px;"><strong>次アクション</strong></div>
                  <ul class="nas">
                    {% for na in t.next_actions %}
                      <li>{{ na }}</li>
                    {% endfor %}
                  </ul>
                {% endif %}

                {% if t.evidence_urls and t.evidence_urls|length>0 %}
                  <div class="small" style="margin-top:6px;">
                    根拠：
                    {% for u in t.evidence_urls %}
                      <a href="{{ u }}" target="_blank" rel="noopener">{{ u }}</a>{% if not loop.last %}, {% endif %}
                    {% endfor %}
                  </div>
                {% endif %}
              </div>
            {% endif %}
          </li>
        {% endfor %}
      </ul>
    {% else %}
      <div class="meta">該当なし</div>
    {% endif %}
  {% endfor %}
</body>
</html>
"""

NAME_MAP = {
    "system": "システム",
    "manufacturing": "製造",
    "security": "セキュリティ",
    "ai": "AI",
    "dev": "開発",
    "other": "その他",
}


def _safe_json_list(s: str | None) -> List[str]:
    if not s:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
    except Exception:
        pass
    return []


def load_categories_from_yaml() -> List[Dict[str, str]]:
    """
    src/sources.yaml の categories を読み取る。
    categories が無い/壊れている場合は空配列を返す。
    """
    try:
        with open("src/sources.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cats = cfg.get("categories")
        if isinstance(cats, list):
            out: List[Dict[str, str]] = []
            for c in cats:
                if isinstance(c, dict) and "id" in c and "name" in c:
                    out.append({"id": str(c["id"]), "name": str(c["name"])})
            return out
    except Exception:
        return []
    return []


def build_categories_fallback(cur) -> List[Dict[str, str]]:
    """
    YAMLが無い場合でも表示が空にならないよう、DBからカテゴリを推定する。
    """
    cur.execute("SELECT DISTINCT category FROM topics WHERE category IS NOT NULL AND category != ''")
    cats = [r[0] for r in cur.fetchall()]
    if not cats:
        cur.execute("SELECT DISTINCT category FROM articles WHERE category IS NOT NULL AND category != ''")
        cats = [r[0] for r in cur.fetchall()]
    if not cats:
        cats = ["other"]
    return [{"id": c, "name": NAME_MAP.get(c, c)} for c in cats]


def ensure_category_coverage(cur, categories: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    YAMLのカテゴリに存在しないカテゴリがDBにある場合でも、表示対象に追加する。
    """
    ids = {c["id"] for c in categories}
    cur.execute("SELECT DISTINCT category FROM topics WHERE category IS NOT NULL AND category != ''")
    db_cats = [r[0] for r in cur.fetchall()]
    for c in db_cats:
        if c not in ids:
            categories.append({"id": c, "name": NAME_MAP.get(c, c)})
            ids.add(c)
    if not categories:
        categories = [{"id": "other", "name": NAME_MAP["other"]}]
    return categories


def main():
    conn = connect()
    cur = conn.cursor()

    # categories: YAML -> DB -> other
    categories = load_categories_from_yaml()
    if not categories:
        categories = build_categories_fallback(cur)
    categories = ensure_category_coverage(cur, categories)

    LIMIT_PER_CAT = 20
    HOT_TOP_N = 5

    topics_by_cat: Dict[str, List[Dict[str, Any]]] = {}
    hot_by_cat: Dict[str, List[Dict[str, Any]]] = {}
    cutoff_48h = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")

    for cat in categories:
        cat_id = cat["id"]

        # (A) 注目TOP5（48h増分、published_atベース）
        if cat_id == "other":
            cur.execute(
                """
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS ttitle,
                  COUNT(ta.article_id) AS total_count,
                  SUM(
                    CASE
                      WHEN COALESCE(NULLIF(a.published_at,''), a.fetched_at) >= ? THEN 1
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
                (cutoff_48h, HOT_TOP_N),
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
                      WHEN COALESCE(NULLIF(a.published_at,''), a.fetched_at) >= ? THEN 1
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
                (cutoff_48h, cat_id, HOT_TOP_N),
            )


        rows = cur.fetchall()
        hot_by_cat[cat_id] = [
            {"id": tid, "title": title, "articles": int(total), "recent": int(recent)}
            for (tid, title, total, recent) in rows
        ]

        # (B) 一覧（topics + insights + 代表URL + 48h増分）
        if cat_id == "other":
            cur.execute(
                """
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS title,
                  (
                    SELECT a2.url
                    FROM topic_articles ta2
                    JOIN articles a2 ON a2.id = ta2.article_id
                    WHERE ta2.topic_id = t.id
                    ORDER BY COALESCE(NULLIF(a2.published_at,''), a2.fetched_at) DESC, a2.id DESC
                    LIMIT 1
                  ) AS url,
                  (
                    SELECT SUM(
                      CASE
                        WHEN COALESCE(NULLIF(a3.published_at,''), a3.fetched_at) >= ? THEN 1
                        ELSE 0
                      END
                    )
                    FROM topic_articles ta3
                    JOIN articles a3 ON a3.id = ta3.article_id
                    WHERE ta3.topic_id = t.id
                  ) AS recent,
                  i.importance,
                  i.summary,
                  i.key_points,
                  i.impact_guess,
                  i.next_actions,
                  i.evidence_urls
                FROM topics t
                LEFT JOIN topic_insights i ON i.topic_id = t.id
                WHERE t.category IS NULL OR t.category = ''
                ORDER BY COALESCE(i.importance, 0) DESC, COALESCE(recent, 0) DESC, t.id DESC
                LIMIT ?
                """,
                (cutoff_48h, LIMIT_PER_CAT),
            )
        else:
            cur.execute(
                """
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS title,
                  (
                    SELECT a2.url
                    FROM topic_articles ta2
                    JOIN articles a2 ON a2.id = ta2.article_id
                    WHERE ta2.topic_id = t.id
                    ORDER BY COALESCE(NULLIF(a2.published_at,''), a2.fetched_at) DESC, a2.id DESC
                    LIMIT 1
                  ) AS url,
                  (
                    SELECT SUM(
                      CASE
                        WHEN COALESCE(NULLIF(a3.published_at,''), a3.fetched_at) >= ? THEN 1
                        ELSE 0
                      END
                    )
                    FROM topic_articles ta3
                    JOIN articles a3 ON a3.id = ta3.article_id
                    WHERE ta3.topic_id = t.id
                  ) AS recent,
                  i.importance,
                  i.summary,
                  i.key_points,
                  i.impact_guess,
                  i.next_actions,
                  i.evidence_urls
                FROM topics t
                LEFT JOIN topic_insights i ON i.topic_id = t.id
                WHERE t.category = ?
                ORDER BY COALESCE(i.importance, 0) DESC, COALESCE(recent, 0) DESC, t.id DESC
                LIMIT ?
                """,
                (cutoff_48h, cat_id, LIMIT_PER_CAT),
            )


        rows = cur.fetchall()
        items: List[Dict[str, Any]] = []
        for r in rows:
            tid, title, url, recent, importance, summary, key_points, impact_guess, next_actions, evidence_urls = r
            items.append(
                {
                    "id": tid,
                    "title": title,  # ← ここはSQLで title_ja 優先済み
                    "url": url or "#",
                    "recent": int(recent) if recent is not None else None,
                    "importance": int(importance) if importance is not None else None,
                    "summary": summary or "",
                    "key_points": _safe_json_list(key_points),
                    "impact_guess": impact_guess or "",
                    "next_actions": _safe_json_list(next_actions),
                    "evidence_urls": _safe_json_list(evidence_urls),
                }
            )

        topics_by_cat[cat_id] = items

    conn.close()

    out_dir = Path("docs")
    out_dir.mkdir(exist_ok=True)
    (out_dir / "index.html").write_text(
        Template(HTML).render(categories=categories, topics_by_cat=topics_by_cat, hot_by_cat=hot_by_cat),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
