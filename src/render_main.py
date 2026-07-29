# src/render.py
from __future__ import annotations
import os
import re

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

import yaml
from jinja2 import Template, Environment, FileSystemLoader
from datetime import datetime, timedelta, timezone

from db import connect
from text_clean import clean_for_html, clean_json_like

from typing import Any, List
import logging
import time

# 外部化したJinja2テンプレート（src/templates/）を読み込むためのEnvironment
# render_main.py の巨大インラインHTML文字列を段階的にテンプレートファイルへ移す一環
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR))

# レンダリング中の非致命エラーを集計するためのグローバルリスト
# 各要素は (section, error_message) のタプル
_render_errors: list[tuple[str, str]] = []

# logging は既定で WARNING 以上を stderr に出す。コード全体で共用する。
_logger = logging.getLogger("render_main")
if not _logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)


def _log_render_error(section: str, err: BaseException | str, *, level: str = "warning") -> None:
    """レンダリング中の非致命エラーを統一ルートで記録する。

    section: どの処理箇所で発生したかを示すラベル（例: "news.fetch", "ops.feed_health"）
    err: 例外または文字列
    level: "debug" | "info" | "warning" | "error"（既定 warning）
    """
    msg = f"{section}: {err}" if err else section
    level_fn = getattr(_logger, level, _logger.warning)
    # 例外オブジェクトならスタックトレースも出す
    if isinstance(err, BaseException):
        _logger.log(
            getattr(logging, level.upper(), logging.WARNING),
            msg,
            exc_info=True,
        )
    else:
        level_fn(msg)
    _render_errors.append((section, str(err)))


def fmt_date(s):
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z",""))
        return dt.astimezone(timezone(timedelta(hours=9))).strftime("%Y/%m/%d %H:%M")
    except (ValueError, TypeError, OSError):
        return str(s)[:16]

def _now_sec():
    return time.perf_counter()


def build_asset_paths(base_path: str = "/daily-tech-trend/") -> dict[str, str]:
    """Return absolute asset paths under the GitHub Pages project base path."""
    normalized_base = f"/{base_path.strip('/')}/"
    return {
        "common_css_href": f"{normalized_base}assets/css/common.css",
        "common_js_src": f"{normalized_base}assets/js/common.js",
        "nav_prefix": normalized_base,
    }

COMMON_CSS = r"""
:root{
  /* Base */
  --bg: #fff;
  --panel: #f9fafb;
  --text-main: #1f2937;
  --text-sub: #6b7280;
  --border: #e5e7eb;

  /* Accent */
  --accent: #2563eb;
  --accent-soft: #dbeafe;

  /* Status */
  --new: #dc2626;
  --new-soft: #fee2e2;

  --important: #f59e0b;
  --important-soft: #fef3c7;
}
body{
  background:var(--bg);
  color:var(--text-main);
}
h1{margin:0 0 10px}
h2{margin:22px 0 10px}
.meta{color:var(--text-sub);font-size:12px;margin:6px 0 14px}
.nav{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 18px}
.nav a{display:inline-block;border:1px solid var(--border);border-radius:999px;padding:6px 10px;text-decoration:none;color:#111}
.nav a.active{border-color:#333;font-weight:700}
.meta,.small{
  color:var(--text-sub);
}

.card{
  background:var(--panel);
  border:1px solid var(--border);
}

.badge{
  border:1px solid var(--border);
  color:var(--text-main);
}
.badge.recent{
  background:var(--new-soft);
  color:var(--new);
  border-color:var(--new);
  font-weight:700;
}
.btn{padding:6px 10px;border:1px solid var(--border);border-radius:10px;background:var(--bg);cursor:pointer;display:inline-block;text-decoration:none}
.btn:hover{background:var(--panel)}
ul{margin:0;padding-left:18px}
li{margin:10px 0}
a{color:inherit}
a{
  color:inherit;
  text-decoration:none;
}

a:hover{
  color:var(--accent);
}

.nav a.active{
  color:var(--accent);
  border-color:var(--accent);
  font-weight:700;
}

"""
TECH_EXTRA_CSS = r"""
/* techの箱・構造を定義している部分をここへ集約 */
.summary-card, .topbox, .top-col, .insight{
  background:#fafafa;
  border:1px solid var(--border);
  border-radius:12px;
  padding:12px 14px;
}
.top-col{ background:var(--bg); }

/* techの見出し間隔・小文字 */
.small{color:#666;font-size:12px}
.badge{display:inline-block;border:1px solid var(--border);border-radius:999px;padding:2px 8px;font-size:12px;color:#444;margin-left:6px}

/* もしtechにタグの見た目があるなら寄せる */
.tag{display:inline-block;border:1px solid var(--border);border-radius:999px;padding:2px 8px;font-size:12px;color:#444;margin-left:6px}


     /* --- UX改善①: 上部サマリー + 横断TOP --- */
    .summary-card{background:#fafafa;border:1px solid var(--border);border-radius:12px;padding:12px 14px;margin:10px 0 14px}
    .summary-title{font-weight:800;font-size:16px;margin:0 0 6px}
    .summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:8px}
    .summary-item .k{color:#666;font-size:11px}
    .summary-item .v{font-size:13px;font-weight:650}

    .top-zone{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0 18px}
    .top-col{background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:10px 12px}
    .top-col h3{margin:0 0 8px;font-size:14px}
    .top-list{margin:0;padding-left:18px}
    .mini{color:#666;font-size:12px;margin-top:2px}
    .source-table{width:100%;border-collapse:collapse;font-size:13px}
    .source-table th,.source-table td{padding:6px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
    .source-table th.num,.source-table td.num{text-align:right;white-space:nowrap}
    .warn-text{color:#b91c1c;font-weight:700}
    .metric-note{margin:6px 0 10px;font-size:12px;color:#4b5563;line-height:1.6}

    .quick-controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:10px}
    #q{padding:6px 10px;border:1px solid var(--border);border-radius:10px;min-width:260px}
    .btn{padding:6px 10px;border:1px solid var(--border);border-radius:10px;background:var(--bg);cursor:pointer}

    .badge.hot{font-weight:800}
    .badge.new{border-style:dashed}

   .badge.imp{
      background:var(--important-soft);
      color:var(--important);
      border-color:var(--important);
    }
    .imp-2{border-color:#6c6}
    .imp-1{border-color:#9ad}
    .imp-0{border-color:#ccc}

    .category-section{margin-top:18px}
    .category-header{display:flex;align-items:center;gap:10px}
    .category-body{margin-top:8px}
    .category-section.collapsed .category-body{display:none}
    
    /* ===== Mobile-first overrides ===== */
    h1{font-size:26px}
    h2{font-size:18px}

    .summary-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
    .top-zone{grid-template-columns:1fr}
    .top-list{padding-left:18px}
    .top-item, .topic-row{margin:10px 0}

    /* 長いタイトル対策（はみ出し防止） */
    .topic-link, a{
      display:inline;
      overflow-wrap:anywhere;
      word-break:break-word;
    }

    /* 検索・フィルタは縦積み気味に */
    .quick-controls{gap:8px}
    #q{min-width:0; width:100%}
    .quick-controls label{font-size:12px}
    .btn{padding:8px 12px} /* タップ領域増 */

    /* カテゴリ見出し周り */
    .category-header{gap:8px}
    .category-header .btn{margin-left:auto}

    /* スマホで「今日の要点」ゾーンを見やすく */
    .summary-card{padding:12px}
    .top-col{padding:10px}

    /* 画面幅が広い時だけPCレイアウトへ */
    @media (min-width: 820px){
      body{margin:24px; font-size:16px}
      .summary-grid{grid-template-columns:repeat(4,minmax(0,1fr))}
      .top-zone{grid-template-columns:1fr 1fr}
      #q{width:auto; min-width:260px}
    }

    /* デフォルト（スマホ）：固定しない */
    .summary-card{
      position: static;
    }

    /* PCサイズ以上のみ固定 */
    @media (min-width: 820px){
      .summary-card{
        position: sticky;
        top: 8px;
        z-index: 10;
      }
    }
    details.insight { margin-top:6px; }
    details.insight > summary {
      cursor:pointer;
      list-style:none;
    }
    details.insight > summary::-webkit-details-marker {
      display:none;
    }
    details.insight > summary::before {
      content:"▶ ";
    }
    details.insight[open] > summary::before {
      content:"▼ ";
    }
    /* details 展開時の視認性向上 */
    details.insight {
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 6px 8px;
      background: var(--bg);
    }

    details.insight[open] {
      background: #f7faff;           /* 薄い青 */
      border-color: #dbe7ff;
    }

    /* summary（トグル）の見た目 */
    details.insight > summary {
      cursor: pointer;
      padding: 4px 0;
      font-weight: 500;
    }

    details.insight > summary::-webkit-details-marker {
      display: none;
    }

    /* 開閉アイコン */
    details.insight > summary::before {
      content: "▶ ";
      color: #4c6ef5;
    }
    details.insight[open] > summary::before {
      content: "▼ ";
    }

    /* 展開後の中身の余白 */
    details.insight[open] > *:not(summary) {
      margin-top: 6px;
    }
    /* 開いている要約だけ影を付ける */
    details.insight[open] {
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
    }

    /* スマホで浮きすぎないように微調整 */
    @media (max-width: 640px) {
      details.insight[open] {
        box-shadow: 0 3px 10px rgba(0, 0, 0, 0.07);
      }
    }
    /* ジャンプ時に sticky に隠れない */
    .topic-row { scroll-margin-top: 88px; }
    
    #filter-count { color:#555; }
    
    #filter-hint { color:#666; }
    #filter-hint strong { color:#444; }

    .nas .small{display:block;margin-top:2px}

    .close-floating{
      position: fixed;
      right: 16px;
      bottom: 16px;
      z-index: 9999;
      padding: 10px 14px;
      border-radius: 999px;
    }
    .category-header{
      position: sticky;
      top: 0;
      z-index: 10;
      background: var(--bg); /* 背景必須 */
    }

    /* Tag bar: wrap + mobile collapse */
    .tag-bar{
      display:flex;
      flex-wrap:wrap;
      gap:6px;
      align-items:center;
    }

    .btn-reset{
      background:#f5f5f5;
      border:1px solid #ccc;
      font-weight:700;
    }

    .btn-more{
      background:var(--bg);
      border:1px dashed var(--border);
      font-weight:650;
    }

    /* スマホ時：初期は7個まで表示（Reset + OR + タグ群含めて調整可） */
    @media (max-width: 640px){
      #tagBar.collapsed button:nth-of-type(n+8){
        display:none;
      }
      /* ORチェックのラベルは常に見せたいなら、上のnth-of-type対象外にするため別classで扱う */
      .tag-mode{ margin-left:4px; }
    }
    .date{
      margin-left: 6px;
      font-size: 0.85em;
      color: #666;
      white-space: nowrap;
    }
"""

NAME_MAP = {
    "system": "システム",
    "manufacturing": "製造",
    "blast_furnace": "高炉",
    "eaf": "電炉",
    "rolling": "圧延",
    "quality": "品質",
    "maintenance": "保全",
    "market": "市況",
    "security": "セキュリティ",
    "ai": "AI",
    "dev": "開発",
    "other": "その他",
}
from typing import Any, List

def _safe_json_list(s: str | None) -> List[str]:
    """list[str] を想定（key_points / evidence_urls 用）"""
    if not s:
        return []
    try:
        v = clean_json_like(json.loads(s))
        if isinstance(v, list):
            out = []
            for x in v:
                if x is None:
                    continue
                out.append(clean_for_html(str(x)))
            return out
    except Exception as e:
        _log_render_error("safe_json_list.parse", e, level="debug")
    return []


def load_categories_from_yaml() -> List[Dict[str, str]]:
    try:
        with open("src/sources.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cats = cfg.get("categories")
        if isinstance(cats, list):
            out = []
            for c in cats:
                if isinstance(c, dict) and "id" in c and "name" in c:
                    out.append({"id": str(c["id"]), "name": str(c["name"])})
            return out
    except Exception as e:
        _log_render_error("load_categories_from_yaml", e, level="warning")
        return []
    return []

def _safe_json_obj(s: str | None) -> Dict[str, Any]:
    if not s:
        return {}
    try:
        v = clean_json_like(json.loads(s))
        return v if isinstance(v, dict) else {}
    except Exception as e:
        _log_render_error("safe_json_obj.parse", e, level="debug")
        return {}


def _tech_has_insight(t: Dict[str, Any]) -> bool:
    """tech.html 277行目の details.insight 表示条件と一致させる（トップページ軽量化）"""
    return bool(
        t.get("summary")
        or t.get("key_points")
        or t.get("perspectives")
        or t.get("perspective_digest")
        or t.get("evidence_urls")
    )


def _news_has_insight(it: Dict[str, Any]) -> bool:
    """news.html 153行目の details.insight 表示条件と一致させる（トップページ軽量化）"""
    persp = it.get("perspectives") or {}
    pdig = it.get("perspective_digest") or {}
    return bool(
        it.get("importance_basis")
        or it.get("summary")
        or it.get("key_points")
        or (persp and (persp.get("engineer") or persp.get("management") or persp.get("consumer")))
        or (pdig and (pdig.get("engineer") or pdig.get("management") or pdig.get("consumer")))
    )


def _insight_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    """details.insight の中身をJSONへ切り出す用のペイロード（トップページ軽量化）"""
    payload: Dict[str, Any] = {}
    if item.get("summary"):
        payload["summary"] = item["summary"]
    if item.get("key_points"):
        payload["key_points"] = item["key_points"]
    if item.get("perspectives"):
        payload["perspectives"] = item["perspectives"]
    if item.get("perspective_digest"):
        payload["perspective_digest"] = item["perspective_digest"]
    if item.get("evidence_urls"):
        payload["evidence_urls"] = item["evidence_urls"]
    if item.get("importance_basis"):
        payload["importance_basis"] = item["importance_basis"]
    return payload


def write_insights_json(out_dir: Path, name: str, items_by_id: Dict[str, Dict[str, Any]]) -> Path:
    """insights_{name}.json を docs/assets/data/ へ書き出す（トップページ軽量化・details.insight遅延ロード用）"""
    data_dir = Path(out_dir) / "assets" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"insights_{name}.json"
    path.write_text(
        json.dumps(items_by_id, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def _news_importance_basis_simple(importance: int, dt: str, category: str, tags: List[str]) -> str:
    imp = int(importance or 0)
    freshness = "通常"
    try:
        now = datetime.now(timezone.utc)
        d = datetime.fromisoformat((dt or "").replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        age_h = max(0.0, (now - d).total_seconds() / 3600.0)
        if age_h <= 6:
            freshness = "高"
        elif age_h <= 24:
            freshness = "中"
        else:
            freshness = "低"
    except Exception as e:
        _log_render_error("news_importance_basis.freshness", e, level="debug")

    cat_weight = {
        "security": "高",
        "policy": "中",
        "market": "中",
        "industry": "中",
        "company": "低",
        "news": "低",
    }.get((category or "").lower(), "低")
    related = len(tags or [])
    return f"importance={imp} / 速報性:{freshness} / カテゴリ重み:{cat_weight} / 関連:{related}タグ"


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


def _extract_domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception as e:
        _log_render_error("extract_domain", e, level="debug")
        return ""


def render_news_pages(out_dir: Path, generated_at: str, cur) -> None:
    news_dir = out_dir / "news"
    news_dir.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    cutoff_48h_str = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")


    # 1) Japan / Global はカテゴリ見出しで分割
    jp_limit_each = NEWS_REGION_LIMIT_EACH.get("jp", 30)
    gl_limit_each = NEWS_REGION_LIMIT_EACH.get("global", 30)
    sections_jp = render_news_region_page(
        cur,
        "jp",
        limit_each=jp_limit_each,
        cutoff_dt=cutoff_48h_str,
        min_per_category=NEWS_MIN_PER_CATEGORY.get("jp", 0),
    )
    sections_gl = render_news_region_page(
        cur,
        "global",
        limit_each=gl_limit_each,
        cutoff_dt=cutoff_48h_str,
        min_per_category=NEWS_MIN_PER_CATEGORY.get("global", 0),
    )

    # Tag list（source中心 + category/regionも混ぜる）
    tag_count = {}
    def add_tags_from_sections(sections, region_label):
        for sec in sections:
            cat = sec.get("anchor") or "other"
            for it in sec.get("rows", []):
                src = it.get("source") or ""
                tags = [region_label, cat]
                if src:
                    tags.append(src)
                for tg in tags:
                    tag_count[tg] = tag_count.get(tg, 0) + 1

    add_tags_from_sections(sections_jp, "jp")
    add_tags_from_sections(sections_gl, "global")
    tag_list_news = sorted(tag_count.items(), key=lambda x: (-x[1], x[0]))[:50]

    # meta（summary-cardに表示する）
    news_total = sum(s["count"] for s in sections_jp) + sum(s["count"] for s in sections_gl)
    news_new48 = sum(s.get("recent48", 0) for s in sections_jp) + sum(s.get("recent48", 0) for s in sections_gl)
    meta_news = {
        "generated_at_jst": generated_at,
        "total_articles": news_total,
        "new_articles_48h": news_new48,
        "jp_count": sum(s["count"] for s in sections_jp),
        "global_count": sum(s["count"] for s in sections_gl),
    }


    # 2) 総合は「全国」「世界」の2セクションにしてまず成立させる（最小）
    #    ※将来、カテゴリ横断にしたくなったらここを拡張
    def flatten(sections, limit=999):
        out = []
        for sec in sections:
            out.extend(sec.get("rows", []))   # ★ rows
        return out[:limit]


    sections_all = [
        {
            "anchor": "jp",
            "title": "🇯🇵 国内ニュース",
            "count": sum(s["count"] for s in sections_jp),
            "recent48": sum(s.get("recent48", 0) for s in sections_jp),
            "rows": flatten(sections_jp, 999),
        },
        {
            "anchor": "global",
            "title": "🌍 世界ニュース",
            "count": sum(s["count"] for s in sections_gl),
            "recent48": sum(s.get("recent48", 0) for s in sections_gl),
            "rows": flatten(sections_gl, 999),
        },
    ]
    # --- ここから差し替え ---

    # # sections_jp / sections_gl は「NEWS_SECTIONS順の配列」なので
    # # そのまま “カテゴリ別セクション” として使う（総合ページ用に地域プレフィックスだけ付ける）
    # def with_prefix(sections, prefix, anchor_prefix):
    #     out = []
    #     for sec in sections:
    #         out.append({
    #             "anchor": f"{anchor_prefix}-{sec['anchor']}",         # 例: jp-manufacturing
    #             "title": f"{prefix} {sec['title']}",                  # 例: 🇯🇵 製造業・鉄鋼...
    #             "count": sec["count"],
    #             "recent48": sec.get("recent48", 0),
    #             "rows": sec.get("rows", []),
    #         })
    #     return out

    # sections_all = (
    #     with_prefix(sections_jp, "🇯🇵", "jp")
    #     + with_prefix(sections_gl, "🌍", "global")
    # )

    # --- ここまで差し替え ---

    # トップページ軽量化: details.insight の中身を insights_news.json へ切り出す
    insights_news = {
        str(it["id"]): _insight_payload(it)
        for sec in sections_all
        for it in sec.get("rows", [])
        if it.get("has_insight")
    }
    write_insights_json(out_dir, "news", insights_news)

    pages = [
        ("news",   "ニュースダイジェスト", "ニュースダイジェスト", sections_all, "index.html"),
    ]

    news_assets = build_asset_paths()

    for page, title, heading, sections, filename in pages:
        (news_dir / filename).write_text(
            _jinja_env.get_template("news.html").render(
                common_css_href=news_assets["common_css_href"],
                common_js_src=news_assets["common_js_src"],

                page=page,
                nav_prefix=news_assets["nav_prefix"], 
                title=title,
                heading=heading,
                generated_at=generated_at,

                meta=meta_news,
                tag_list=tag_list_news,

                sections=sections,
            ),

            encoding="utf-8",
        )

    # 立場別意見ページは廃止済み（未来予測ページに統合）

NEWS_SECTIONS = [
    ("news",          "一般ニュース（未分類）"),
    ("manufacturing", "製造業・鉄鋼（現場/プラント）"),
    ("policy",        "政策・制度・規制"),
    ("security",      "セキュリティ/事故"),
    ("industry",      "産業・市況・サプライチェーン"),
    ("company",       "企業動向（提携/投資/決算）"),
    ("other",         "その他"),
]

NEWS_SECTION_POINTS = {
    "news": "社会・産業全体の動き。技術導入や投資判断の背景として確認。",
    "manufacturing": "現場改善・省人化・品質保証に直結。設備更新やDX提案の根拠。",
    "policy": "制度変更・規制強化の兆し。中長期のIT投資・対応計画に影響。",
    "security": "事業継続・リスク管理の観点。対策投資の説明材料。",
    "industry": "市況・サプライチェーン変化。需要予測やシステム刷新の背景。",
    "company": "競合・先行事例。顧客への『他社事例』として利用可能。",
    "other": "個別要因。将来の技術動向と結び付けて整理。",
}

NEWS_REGION_LIMIT_EACH = {
    "jp": 50,
    "global": 30,
}

NEWS_MIN_PER_CATEGORY = {
    "jp": 8,
    "global": 0,
}

def render_news_region_page(cur, region, limit_each=30, cutoff_dt=None, min_per_category=0):
    sections = []
    for cat, title in NEWS_SECTIONS:
        fetch_limit = max(limit_each, min_per_category or 0)
        rows = fetch_news_articles_by_category(cur, region, cat, fetch_limit)

        # 国内不足時はカテゴリ内の最小件数を満たすまで追加取得する
        while (
            min_per_category
            and len(rows) < min_per_category
            and len(rows) >= fetch_limit
            and fetch_limit < 200
        ):
            fetch_limit += max(limit_each, min_per_category)
            rows = fetch_news_articles_by_category(cur, region, cat, fetch_limit)
        items = []
        for r in rows:
            # fetch_news_articles_by_category() のSELECT順に合わせる
            # （現実装: 代表フラグあり=17列 / 旧互換=16列）
            if len(r) >= 17:
                (
                    article_id, title, url, source, category, published_at, fetched_at, dt,
                    importance, typ, summary, key_points, perspectives, perspective_digest, tags, evidence_urls,
                    is_representative,
                ) = r
            else:
                (
                    article_id, title, url, source, category, published_at, fetched_at, dt,
                    importance, typ, summary, key_points, perspectives, perspective_digest, tags, evidence_urls,
                ) = r
                is_representative = 0

            # フィルタ/検索向けのタグは LLM tags を優先し、無い場合はフォールバック
            llm_tags = _safe_json_list(tags)
            if not llm_tags:
                llm_tags = [region, (category or cat or "other")]
                if source:
                    llm_tags.append(source)

            items.append({
                "id": int(article_id),
                "title": clean_for_html(title),
                "url": clean_for_html(url),
                "source": clean_for_html(source),
                "category": clean_for_html(category or cat or "other"),  # ←追加
                "dt": clean_for_html(dt),                                # ←追加（data-date用）
                "dt_jst": fmt_date(dt),
                "published_at": clean_for_html(published_at),
                "published_at_jst": fmt_date(published_at),
                "fetched_at": clean_for_html(fetched_at),
                "fetched_at_jst": fmt_date(fetched_at),

                # LLM結果
                "importance": int(importance) if importance is not None else 0,
                "type": clean_for_html(typ or ""),
                "summary": clean_for_html(summary or ""),
                "key_points": _safe_json_list(key_points),
                "perspectives": _safe_json_obj(perspectives),
                "perspective_digest": _safe_json_obj(perspective_digest),
                "tags": llm_tags,
                "evidence_urls": _safe_json_list(evidence_urls),
                "is_representative": int(is_representative or 0),
                "importance_basis": _news_importance_basis_simple(
                    int(importance) if importance is not None else 0,
                    dt,
                    category or cat or "other",
                    llm_tags,
                ),
            })
            items[-1]["has_insight"] = _news_has_insight(items[-1])

        # 重要度0（LLM未分析）の記事を除外
        items = [it for it in items if it.get("importance", 0) > 0]

        items.sort(key=lambda x: (x.get("is_representative", 0), x.get("dt") or "", x.get("id", 0)), reverse=True)
        representative_items = [it for it in items if it.get("is_representative", 0) == 1]
        other_items = [it for it in items if it.get("is_representative", 0) != 1]

        recent48 = 0
        if cutoff_dt:
            recent48 = count_news_recent_48h(cur, region, cat, cutoff_dt)

        TECH_LINK_MAP = {
            "manufacturing": ("manufacturing", "製造業・現場DX"),
            "security": ("security", "セキュリティ"),
            "policy": ("system", "制度・ガバナンス"),
            "industry": ("system", "基幹・業務システム"),
            "company": ("dev", "開発・内製化"),
        }

        tech_link = TECH_LINK_MAP.get(cat)

        sections.append({
            "title": clean_for_html(title),
            "count": len(items),
            "rep_count": len(representative_items),
            "recent48": recent48,
            "point": NEWS_SECTION_POINTS.get(cat, ""),
            "rows": representative_items,
            "other_rows": other_items,
            "anchor": cat,
            "tech_link": tech_link[0] if tech_link else None,
            "tech_label": tech_link[1] if tech_link else None,
        })

    return sections


def fetch_news_articles(cur, region: str, limit: int = 60):
    if region:
        cur.execute(
            """
            SELECT
              articles.id AS article_id,
              COALESCE(NULLIF(title_ja,''), NULLIF(title,''), url) AS title,
              url,
              COALESCE(NULLIF(source,''), '') AS source,
              COALESCE(NULLIF(category,''), '') AS category,
              COALESCE(NULLIF(published_at,''), fetched_at) AS dt,

              (
                SELECT i.importance
                FROM topic_articles ta
                JOIN topic_insights i ON i.topic_id = ta.topic_id
                WHERE ta.article_id = articles.id
                ORDER BY i.importance DESC, ta.topic_id DESC
                LIMIT 1
              ) AS importance,
              (
                SELECT i.tags
                FROM topic_articles ta
                JOIN topic_insights i ON i.topic_id = ta.topic_id
                WHERE ta.article_id = articles.id
                ORDER BY i.importance DESC, ta.topic_id DESC
                LIMIT 1
              ) AS tags,
              (
                SELECT i.summary
                FROM topic_articles ta
                JOIN topic_insights i ON i.topic_id = ta.topic_id
                WHERE ta.article_id = articles.id
                ORDER BY i.importance DESC, ta.topic_id DESC
                LIMIT 1
              ) AS summary

            FROM articles
            WHERE kind='news' AND region=?
            ORDER BY
              COALESCE(importance, 0) DESC,
              datetime(
                substr(
                  replace(replace(COALESCE(NULLIF(published_at,''), fetched_at),'T',' '),'+00:00',''),
                  1, 19
                )
              ) DESC,
              id DESC
            LIMIT ?
            """,
            (region, limit),
        )
    else:
        cur.execute(
            """
            SELECT
              COALESCE(NULLIF(title_ja,''), NULLIF(title,''), url) AS title,
              url,
              COALESCE(NULLIF(source,''), '') AS source,
              COALESCE(NULLIF(category,''), '') AS category,
              COALESCE(NULLIF(region,''), '') AS region,
              COALESCE(NULLIF(published_at,''), fetched_at) AS dt
            FROM articles
            WHERE kind='news'
            ORDER BY
              datetime(
                substr(
                  replace(replace(COALESCE(NULLIF(published_at,''), fetched_at),'T',' '),'+00:00',''),
                  1, 19
                )
              ) DESC,
              id DESC
            LIMIT ?
            """,
            (limit,),
        )

    return cur.fetchall()

def fetch_news_articles_by_category(cur, region: str, category: str, limit: int = 40):
    cur.execute(
        """
        SELECT
          a.id AS article_id,
          COALESCE(NULLIF(a.title_ja,''), NULLIF(a.title,''), a.url) AS title,
          a.url,
          COALESCE(NULLIF(a.source,''), '') AS source,
          COALESCE(NULLIF(a.category,''), '') AS category,
          COALESCE(NULLIF(a.published_at,''), '') AS published_at,
          COALESCE(NULLIF(a.fetched_at,''), '') AS fetched_at,
          COALESCE(NULLIF(a.published_at,''), a.fetched_at) AS dt,

          -- 代表insight（importanceが高いtopicを採用）
          (
            SELECT i.importance
            FROM topic_articles ta
            JOIN topic_insights i ON i.topic_id = ta.topic_id
            WHERE ta.article_id = a.id
            ORDER BY i.importance DESC, ta.topic_id DESC
            LIMIT 1
          ) AS importance,
          (
            SELECT i.type
            FROM topic_articles ta
            JOIN topic_insights i ON i.topic_id = ta.topic_id
            WHERE ta.article_id = a.id
            ORDER BY i.importance DESC, ta.topic_id DESC
            LIMIT 1
          ) AS type,
          (
            SELECT i.summary
            FROM topic_articles ta
            JOIN topic_insights i ON i.topic_id = ta.topic_id
            WHERE ta.article_id = a.id
            ORDER BY i.importance DESC, ta.topic_id DESC
            LIMIT 1
          ) AS summary,
          (
            SELECT i.key_points
            FROM topic_articles ta
            JOIN topic_insights i ON i.topic_id = ta.topic_id
            WHERE ta.article_id = a.id
            ORDER BY i.importance DESC, ta.topic_id DESC
            LIMIT 1
          ) AS key_points,
          (
            SELECT i.perspectives
            FROM topic_articles ta
            JOIN topic_insights i ON i.topic_id = ta.topic_id
            WHERE ta.article_id = a.id
            ORDER BY i.importance DESC, ta.topic_id DESC
            LIMIT 1
          ) AS perspectives,
          (
            SELECT i.perspective_digest
            FROM topic_articles ta
            JOIN topic_insights i ON i.topic_id = ta.topic_id
            WHERE ta.article_id = a.id
            ORDER BY i.importance DESC, ta.topic_id DESC
            LIMIT 1
          ) AS perspective_digest,
          (
            SELECT i.tags
            FROM topic_articles ta
            JOIN topic_insights i ON i.topic_id = ta.topic_id
            WHERE ta.article_id = a.id
            ORDER BY i.importance DESC, ta.topic_id DESC
            LIMIT 1
          ) AS tags,
          (
            SELECT i.evidence_urls
            FROM topic_articles ta
            JOIN topic_insights i ON i.topic_id = ta.topic_id
            WHERE ta.article_id = a.id
            ORDER BY i.importance DESC, ta.topic_id DESC
            LIMIT 1
          ) AS evidence_urls,
          CASE
            WHEN EXISTS (
              SELECT 1
              FROM topic_articles ta
              WHERE ta.article_id = a.id
                AND COALESCE(ta.is_representative, 0) = 1
            ) THEN 1
            ELSE 0
          END AS is_representative

        FROM articles a
        WHERE a.kind='news'
          AND a.region=?
          AND COALESCE(NULLIF(a.category,''), 'other')=?
        ORDER BY
          datetime(
            substr(
              replace(replace(COALESCE(NULLIF(a.published_at,''), a.fetched_at),'T',' '),'+00:00',''),
              1, 19
            )
          ) DESC,
          a.id DESC
        LIMIT ?

        """,
        (region, category, limit),
    )
    return cur.fetchall()

def count_news_recent_48h(cur, region: str, category: str, cutoff_dt: str) -> int:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM articles
        WHERE kind='news'
          AND region=?
          AND COALESCE(NULLIF(category,''), 'other')=?
          AND datetime(
                substr(
                  replace(replace(COALESCE(NULLIF(published_at,''), fetched_at),'T',' '),'+00:00',''),
                  1, 19
                )
              ) >= datetime(?)
        """,
        (region, category, cutoff_dt),
    )
    return int(cur.fetchone()[0] or 0)


# ---------------------------------------------------------------------------
# 未来予測ページ
# ---------------------------------------------------------------------------



def _group_past_reports_by_month(reports):
    """過去レポートを年月単位でグルーピングして降順リストを返す。

    入力: [{"date": "2026-05-04"}, ...] (日付降順を想定)
    出力: [{"ym": "2026-05", "label": "2026年5月", "entries": [...]}, ...]
    ※ Jinja2 で dict.items メソッドと衝突するため、キー名は entries にする。
    """
    from collections import OrderedDict
    groups = OrderedDict()
    for r in reports:
        date_str = r.get("date") or ""
        if len(date_str) < 7:
            continue
        ym = date_str[:7]
        groups.setdefault(ym, []).append(r)
    out = []
    for ym, entries in groups.items():
        try:
            year = int(ym[:4])
            month = int(ym[5:7])
            label = f"{year}年{month}月"
        except ValueError:
            label = ym
        out.append({"ym": ym, "label": label, "entries": entries})
    return out


def render_forecast_page(out_dir, generated_at, cur):
    """未来予測ページを生成する"""
    from pathlib import Path
    from forecast_parser import parse_forecast_markdown, ForecastReport

    def _fix_markdown_tables(text):
        """テーブル行間の空行を除去し、ヘッダー区切り行がなければ挿入する"""
        lines = text.split("\n")
        out = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                # テーブルブロックの開始 — 連続するテーブル行を収集
                table_lines = [stripped]
                i += 1
                while i < len(lines):
                    s = lines[i].strip()
                    if s.startswith("|") and s.endswith("|"):
                        table_lines.append(s)
                        i += 1
                    elif s == "":
                        # 空行の次がテーブル行ならスキップして継続
                        if i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
                            i += 1
                            continue
                        break
                    else:
                        break
                # 区切り行チェック（2行目が | --- | --- | 形式か）
                has_sep = len(table_lines) > 1 and all(
                    c in "-| :" for c in table_lines[1]
                )
                if not has_sep and len(table_lines) >= 1:
                    # 1行目の列数に合わせて区切り行を挿入
                    ncols = table_lines[0].count("|") - 1
                    sep = "| " + " | ".join(["---"] * max(ncols, 1)) + " |"
                    table_lines.insert(1, sep)
                out.extend(table_lines)
                out.append("")
            else:
                out.append(line)
                i += 1
        return "\n".join(out)

    try:
        import mistune
        _md_renderer = mistune.create_markdown(plugins=["table"])
        def md_to_html(text):
            return _md_renderer(_fix_markdown_tables(text))
    except ImportError:
        import html as _html

        def md_to_html(text):
            """mistune未インストール時の簡易Markdown→HTML変換"""
            text = _fix_markdown_tables(text)
            lines = _html.escape(text).split("\n")
            out = []
            in_list = False
            in_table = False
            for line in lines:
                stripped = line.strip()
                # 空行
                if not stripped:
                    if in_list:
                        out.append("</ul>")
                        in_list = False
                    if in_table:
                        out.append("</tbody></table>")
                        in_table = False
                    out.append("")
                    continue
                # 見出し
                if stripped.startswith("### "):
                    out.append(f"<h3>{stripped[4:]}</h3>")
                    continue
                if stripped.startswith("## "):
                    out.append(f"<h2>{stripped[3:]}</h2>")
                    continue
                if stripped.startswith("# "):
                    out.append(f"<h1>{stripped[2:]}</h1>")
                    continue
                # 水平線
                if stripped in ("---", "***", "___"):
                    out.append("<hr>")
                    continue
                # テーブル区切り行
                if stripped.startswith("|") and set(stripped.replace("|", "").replace("-", "").replace(" ", "")) == set():
                    continue
                # テーブル行
                if stripped.startswith("|") and stripped.endswith("|"):
                    cells = [c.strip() for c in stripped.strip("|").split("|")]
                    if not in_table:
                        in_table = True
                        out.append('<table class="source-table"><tbody>')
                    out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
                    continue
                # 引用ブロック
                if stripped.startswith("&gt; "):
                    content = stripped[5:]
                    content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
                    content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
                    out.append(f"<blockquote>{content}</blockquote>")
                    continue
                # リスト
                if stripped.startswith("- ") or stripped.startswith("* "):
                    if not in_list:
                        in_list = True
                        out.append("<ul>")
                    content = stripped[2:]
                    content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
                    content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
                    out.append(f"<li>{content}</li>")
                    continue
                # 番号付きリスト
                m_ol = re.match(r"^(\d+)\.\s+(.+)", stripped)
                if m_ol:
                    content = m_ol.group(2)
                    content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
                    content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
                    out.append(f"<p>{m_ol.group(1)}. {content}</p>")
                    continue
                # 通常段落（太字・斜体変換）
                if in_list:
                    out.append("</ul>")
                    in_list = False
                if in_table:
                    out.append("</tbody></table>")
                    in_table = False
                p = stripped
                p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
                p = re.sub(r"\*(.+?)\*", r"<em>\1</em>", p)
                out.append(f"<p>{p}</p>")
            if in_list:
                out.append("</ul>")
            if in_table:
                out.append("</tbody></table>")
            return "\n".join(out)

    # 最新レポートを取得
    cur.execute("""
        SELECT report_date, file_path, executive_summary, accuracy_score
        FROM forecast_reports
        ORDER BY report_date DESC
        LIMIT 1
    """)
    row = cur.fetchone()

    # 過去平均精度スコア
    cur.execute("""
        SELECT AVG(accuracy_score) FROM forecast_reports
        WHERE accuracy_score IS NOT NULL
    """)
    avg_row = cur.fetchone()
    avg_accuracy = round(avg_row[0] * 100, 1) if avg_row and avg_row[0] is not None else ""

    # 時間軸別の的中率（最新レポートの最新ラウンド）
    horizon_accuracy = {}
    try:
        cur.execute("""
            SELECT fv.horizon, fv.accuracy_score
            FROM forecast_verifications fv
            INNER JOIN (
                SELECT report_date, horizon, MAX(verification_round) AS max_round
                FROM forecast_verifications
                WHERE report_date = (SELECT report_date FROM forecast_reports ORDER BY report_date DESC LIMIT 1)
                GROUP BY report_date, horizon
            ) latest
            ON fv.report_date = latest.report_date
               AND fv.horizon = latest.horizon
               AND fv.verification_round = latest.max_round
        """)
        for h, score in cur.fetchall():
            horizon_accuracy[h] = round(score * 100, 1) if score is not None else None
    except Exception as e:
        _log_render_error("forecast.horizon_accuracy_query", e, level="warning")

    # 過去レポート一覧
    cur.execute("""
        SELECT report_date FROM forecast_reports
        ORDER BY report_date DESC
    """)
    past_reports = [{"date": r[0]} for r in cur.fetchall()]

    has_report = False
    report = ForecastReport()
    report_date = "-"
    accuracy_score = ""

    if row:
        report_date = row[0]
        file_path = Path(row[1])
        if row[3] is not None:
            accuracy_score = round(row[3] * 100, 1)
        if file_path.exists():
            md_text = file_path.read_text(encoding="utf-8")
            report = parse_forecast_markdown(md_text)
            has_report = True

    # Markdown → HTML 変換
    executive_summary_html = md_to_html(report.executive_summary) if report.executive_summary else ""
    checked_report_html = md_to_html(report.checked_report) if report.checked_report else ""
    appendix_fc_html = md_to_html(report.appendix_factcheck) if report.appendix_factcheck else ""
    appendix_news_html = md_to_html(report.appendix_news) if report.appendix_news else ""

    from forecast_parser import parse_prediction_items

    prediction_keys = list(report.predictions.keys())
    impact_order = {"大": 0, "中": 1, "小": 2}
    conf_order = {"高": 0, "中": 1, "低": 2}

    # バッジで表示済みの影響度・確信度行を本文から除去するパターン
    _dup_badge_re = re.compile(
        r"\*{0,2}影響度[:：]\s*\S+\s*[/／]\s*確信度[:：]\s*\S+\*{0,2}\s*\n?"
    )

    # 空殻プレースホルダ（"### 1. 予測1" 見出し + 中身空 "- **予測内容**：" と "> **根拠**:"）を
    # 既存レポートで救済するための判定関数。title が "予測N" のデフォルト名かつ body から
    # バッジ・空の定型行・水平線を除くと実内容が残らない場合は、empty-horizon 扱いへ寄せる。
    _empty_body_re = re.compile(r"-\s*\*{0,2}予測内容\*{0,2}[:：]\s*$|>\s*\*{0,2}根拠\*{0,2}[:：]\s*$", re.MULTILINE)
    # Markdown の水平線（--- / *** / ___）やファイル末尾に付く区切りも除去対象にする
    _hr_line_re = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)
    _placeholder_title_re = re.compile(r"^予測\d+$")

    def _is_empty_placeholder(item, body_after_strip):
        """生成失敗時に残る "### 1. 予測1" 空殻を検出する。"""
        if not _placeholder_title_re.match((item.title or "").strip()):
            return False
        residual = _empty_body_re.sub("", body_after_strip)
        residual = _hr_line_re.sub("", residual).strip()
        return residual == ""

    prediction_items = {}
    for k, v in report.predictions.items():
        items = parse_prediction_items(v)
        rendered = []
        for item in items:
            body = _dup_badge_re.sub("", item.body).strip()
            if _is_empty_placeholder(item, body):
                # 空殻アイテムは描画せずスキップ → 結果として horizon が空なら
                # Jinja テンプレート側のフォールバック注記が表示される。
                continue
            rendered.append({
                "impact": item.impact,
                "confidence": item.confidence,
                "title": item.title,
                "body_html": md_to_html(body),
            })
        rendered.sort(key=lambda x: (impact_order.get(x["impact"], 2),
                                      conf_order.get(x["confidence"], 2)))
        prediction_items[k] = rendered

    perspective_keys = list(report.perspectives.keys())
    perspective_htmls = {k: md_to_html(v) for k, v in report.perspectives.items()}

    assets = build_asset_paths()
    forecast_html = _jinja_env.get_template("forecast.html").render(
        page="forecast",
        common_css_href=assets["common_css_href"],
        common_js_src=assets["common_js_src"],
        generated_at=generated_at,
        report_date=report_date,
        has_report=has_report,
        executive_summary_html=executive_summary_html,
        prediction_keys=prediction_keys,
        prediction_items=prediction_items,
        checked_report_html=checked_report_html,
        perspective_keys=perspective_keys,
        perspective_htmls=perspective_htmls,
        appendix_fc_html=appendix_fc_html,
        appendix_news_html=appendix_news_html,
        past_reports_grouped=_group_past_reports_by_month(
            past_reports[1:] if len(past_reports) > 1 else []
        ),
        link_prefix="./",
        accuracy_score=accuracy_score,
        avg_accuracy=avg_accuracy,
        horizon_accuracy=horizon_accuracy,
    )

    forecast_dir = Path(out_dir) / "forecast"
    forecast_dir.mkdir(exist_ok=True)
    (forecast_dir / "index.html").write_text(forecast_html, encoding="utf-8")
    print(f"  forecast page: {forecast_dir / 'index.html'}")

    # 過去レポートの個別HTML生成（最新を除く全件）
    past_to_render = past_reports[1:] if len(past_reports) > 1 else []
    for pr in past_to_render:
        pr_date = pr["date"]
        cur.execute("""
            SELECT report_date, file_path, executive_summary, accuracy_score
            FROM forecast_reports WHERE report_date = ?
        """, (pr_date,))
        pr_row = cur.fetchone()
        if not pr_row:
            continue
        pr_file = Path(pr_row[1])
        if not pr_file.exists():
            continue
        pr_md = pr_file.read_text(encoding="utf-8")
        pr_report = parse_forecast_markdown(pr_md)
        pr_acc = round(pr_row[3] * 100, 1) if pr_row[3] is not None else ""

        pr_exec_html = md_to_html(pr_report.executive_summary) if pr_report.executive_summary else ""
        pr_checked_html = md_to_html(pr_report.checked_report) if pr_report.checked_report else ""
        pr_appx_fc = md_to_html(pr_report.appendix_factcheck) if pr_report.appendix_factcheck else ""
        pr_appx_news = md_to_html(pr_report.appendix_news) if pr_report.appendix_news else ""

        pr_pred_keys = list(pr_report.predictions.keys())
        pr_pred_items = {}
        for k, v in pr_report.predictions.items():
            pitems = parse_prediction_items(v)
            pr_rendered = []
            for pi in pitems:
                body = _dup_badge_re.sub("", pi.body).strip()
                # 現在レポートと同じく空殻プレースホルダはスキップ
                if _is_empty_placeholder(pi, body):
                    continue
                pr_rendered.append({
                    "impact": pi.impact, "confidence": pi.confidence,
                    "title": pi.title, "body_html": md_to_html(body),
                })
            pr_rendered.sort(key=lambda x: (impact_order.get(x["impact"], 2),
                                             conf_order.get(x["confidence"], 2)))
            pr_pred_items[k] = pr_rendered

        pr_persp_keys = list(pr_report.perspectives.keys())
        pr_persp_htmls = {k: md_to_html(v) for k, v in pr_report.perspectives.items()}

        # 過去レポートのリンクは現在と同じ一覧を使う
        other_past = [p for p in past_reports if p["date"] != pr_date]
        pr_html = _jinja_env.get_template("forecast.html").render(
            page="forecast",
            common_css_href=assets["common_css_href"],
            common_js_src=assets["common_js_src"],
            generated_at=generated_at,
            report_date=pr_date,
            has_report=True,
            executive_summary_html=pr_exec_html,
            prediction_keys=pr_pred_keys,
            prediction_items=pr_pred_items,
            checked_report_html=pr_checked_html,
            perspective_keys=pr_persp_keys,
            perspective_htmls=pr_persp_htmls,
            appendix_fc_html=pr_appx_fc,
            appendix_news_html=pr_appx_news,
            past_reports_grouped=_group_past_reports_by_month(other_past),
            link_prefix="../",
            accuracy_score=pr_acc,
            avg_accuracy=avg_accuracy,
            # 過去レポートでは時間軸別的中率を表示しない（現在レポート用の集計）。
            # 未渡しだと Jinja の `horizon_accuracy.get()` で UndefinedError になるため空を渡す。
            horizon_accuracy={},
        )
        pr_dir = forecast_dir / pr_date
        pr_dir.mkdir(exist_ok=True)
        (pr_dir / "index.html").write_text(pr_html, encoding="utf-8")
    if past_to_render:
        print(f"  forecast past pages: {len(past_to_render)}件生成")




def render_forecast_hits_page(out_dir, generated_at, cur):
    """的中と判定された予測のみを集約したページを生成"""
    from pathlib import Path

    # 各(report_date, horizon)の最新ラウンドのみ参照
    cur.execute("""
        SELECT fv.report_date, fv.horizon, fv.verdict_json, fv.verified_at
        FROM forecast_verifications fv
        INNER JOIN (
            SELECT report_date, horizon, MAX(verification_round) AS max_round
            FROM forecast_verifications
            GROUP BY report_date, horizon
        ) latest
        ON fv.report_date = latest.report_date
           AND fv.horizon = latest.horizon
           AND fv.verification_round = latest.max_round
        ORDER BY fv.report_date DESC
    """)
    rows = cur.fetchall()

    # 時間軸の表示順
    horizon_order = ["1週間後", "1〜6ヶ月後", "1年後"]
    hits_by_horizon = {h: [] for h in horizon_order}
    report_dates_with_hits = set()
    total_hits = 0

    for report_date, horizon, verdict_json, verified_at in rows:
        if not verdict_json:
            continue
        try:
            verdicts = json.loads(verdict_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(verdicts, list):
            continue
        for v in verdicts:
            if not isinstance(v, dict):
                continue
            raw_verdict = v.get("verdict")
            acc = v.get("accuracy")
            try:
                acc_num = float(acc) if acc is not None else None
            except (TypeError, ValueError):
                acc_num = None
            # 的中判定: verdict が "的中"/"部分的中"、または accuracy が 0 超
            # （LLMが verdict="未確定" でも accuracy=0.5 を返すケースを救済）
            is_hit = raw_verdict in ("的中", "部分的中")
            if not is_hit and acc_num is not None and acc_num > 0:
                is_hit = True
            if not is_hit:
                continue
            # 表示用ラベル: accuracy に応じて自動判定
            if acc_num is not None and acc_num >= 1.0:
                label = "的中"
            elif acc_num is not None and acc_num > 0:
                label = "部分的中"
            else:
                label = raw_verdict or "的中"
            acc_disp = round(acc_num, 2) if acc_num is not None else None
            verified_disp = (verified_at or "")[:10]
            entry = {
                "title": v.get("title", "(タイトル不明)"),
                "reason": v.get("reason", ""),
                "accuracy": acc_disp,
                "label": label,
                "report_date": report_date,
                "verified_at": verified_disp,
                # 検証時に LLM が抽出した的中根拠（新機能）。古い検証結果には無いため optional。
                "evidence_title": v.get("evidence_title", "") or "",
                "evidence_source": v.get("evidence_source", "") or "",
            }
            hits_by_horizon.setdefault(horizon, []).append(entry)
            report_dates_with_hits.add(report_date)
            total_hits += 1

    # 空の時間軸を除外し、表示順を確定
    ordered = {}
    for h in horizon_order:
        if hits_by_horizon.get(h):
            ordered[h] = hits_by_horizon[h]
    for h, lst in hits_by_horizon.items():
        if h not in ordered and lst:
            ordered[h] = lst

    assets = build_asset_paths()
    html = _jinja_env.get_template("forecast_hits.html").render(
        page="forecast_hits",
        common_css_href=assets["common_css_href"],
        common_js_src=assets["common_js_src"],
        generated_at=generated_at,
        total_hits=total_hits,
        report_count=len(report_dates_with_hits),
        hits_by_horizon=ordered,
    )

    hits_dir = Path(out_dir) / "forecast" / "hits"
    hits_dir.mkdir(parents=True, exist_ok=True)
    (hits_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"  forecast hits page: {hits_dir / 'index.html'} ({total_hits}件)")


# --- RSS / 検索ページは render_feeds.py に分離。後方互換のため再エクスポート ---
from render_feeds import (
    FEED_DESCRIPTION,
    FEED_SITE_URL,
    FEED_TITLE,
    SEARCH_HTML,
    _rss_escape,
    _rss_rfc822,
    render_rss_feed as _render_rss_feed_impl,
    render_search_page as _render_search_page_impl,
    render_sitemap as _render_sitemap_impl,
    render_json_api as _render_json_api_impl,
)


def render_sitemap(out_dir: Path, cur) -> None:
    """互換ラッパー: render_feeds.render_sitemap へ委譲。"""
    return _render_sitemap_impl(out_dir, cur)


def render_json_api(out_dir: Path, cur) -> None:
    """互換ラッパー: render_feeds.render_json_api へ委譲。"""
    return _render_json_api_impl(out_dir, cur)


def render_rss_feed(out_dir: Path, generated_at: str, cur, limit: int = 30) -> None:
    """互換ラッパー: render_feeds.render_rss_feed へ委譲。"""
    return _render_rss_feed_impl(out_dir, generated_at, cur, limit=limit)


def render_search_page(out_dir: Path, generated_at: str, cur, limit: int = 3000) -> None:
    """互換ラッパー: render_feeds.render_search_page へ委譲。"""
    assets = build_asset_paths()
    return _render_search_page_impl(
        out_dir,
        generated_at,
        cur,
        limit=limit,
        common_css_href=assets["common_css_href"],
        nav_prefix=assets["nav_prefix"],
    )


def _build_ops_page_data(cur, cutoff_48h: str, cat_name: Dict[str, str]) -> Dict[str, Any]:
    """ops.html 用の統計データを取得・集計する（main() から分離）。"""
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    # 記事統計
    cur.execute("""
        SELECT COUNT(*) as total,
          SUM(CASE WHEN datetime(fetched_at) >= datetime(?) THEN 1 ELSE 0 END) as week,
          SUM(CASE WHEN datetime(fetched_at) >= datetime(?) THEN 1 ELSE 0 END) as h48
        FROM articles
    """, (cutoff_7d, cutoff_48h))
    row = cur.fetchone()
    ops_stats = {
        "article_total": int(row[0] or 0),
        "article_week": int(row[1] or 0),
        "article_48h": int(row[2] or 0),
    }

    # トピック・インサイト統計
    cur.execute("SELECT COUNT(*) FROM topics")
    ops_stats["topic_total"] = int(cur.fetchone()[0] or 0)
    cur.execute("SELECT COUNT(*) FROM topic_insights")
    ops_stats["insight_count"] = int(cur.fetchone()[0] or 0)
    ops_stats["insight_pending"] = max(0, ops_stats["topic_total"] - ops_stats["insight_count"])

    # 日別収集トレンド（14日）
    cur.execute("""
        SELECT DATE(fetched_at) as d, COUNT(*) as cnt
        FROM articles
        WHERE datetime(fetched_at) >= datetime('now','-14 days')
        GROUP BY DATE(fetched_at)
        ORDER BY d
    """)
    daily_raw = cur.fetchall()
    daily_max = max((r[1] for r in daily_raw), default=1)
    daily_trend = []
    bar_max_px = 100  # バーの最大高さ(px)
    for d, cnt in daily_raw:
        px = max(2, round(cnt / daily_max * bar_max_px)) if daily_max else 2
        label = d[5:] if d else ""  # MM-DD
        daily_trend.append({"d": d, "cnt": cnt, "px": px, "label": label})

    # カテゴリ別（全期間+7日）
    cur.execute("""
        SELECT COALESCE(NULLIF(category,''), 'other') as cat, COUNT(*) as total,
          SUM(CASE WHEN datetime(fetched_at) >= datetime(?) THEN 1 ELSE 0 END) as week
        FROM articles
        GROUP BY cat
        ORDER BY total DESC
    """, (cutoff_7d,))
    cat_raw = cur.fetchall()
    cat_max = max((r[1] for r in cat_raw), default=1)
    category_dist = []
    for cat, total, week in cat_raw:
        pct = round(total / cat_max * 100) if cat_max else 0
        category_dist.append({
            "name": cat_name.get(cat, cat),
            "total": int(total or 0),
            "week": int(week or 0),
            "pct": pct,
        })

    # ソース別記事数TOP15（7日列追加）
    cur.execute("""
        SELECT
          COALESCE(NULLIF(source,''), '') AS source,
          COUNT(*) AS total,
          SUM(CASE WHEN datetime(COALESCE(NULLIF(published_at,''), fetched_at)) >= datetime(?) THEN 1 ELSE 0 END) AS recent7d,
          SUM(CASE WHEN datetime(COALESCE(NULLIF(published_at,''), fetched_at)) >= datetime(?) THEN 1 ELSE 0 END) AS recent48,
          GROUP_CONCAT(DISTINCT COALESCE(NULLIF(category,''), 'other')) AS categories
        FROM articles
        WHERE COALESCE(NULLIF(source,''), '') != ''
        GROUP BY source
        ORDER BY total DESC, recent48 DESC, source ASC
        LIMIT 15
    """, (cutoff_7d, cutoff_48h))
    source_exposure = []
    for source, total, recent7d, recent48, categories_str in cur.fetchall():
        cat_ids = [c for c in (categories_str or "").split(",") if c]
        category_labels = [cat_name.get(c, c) for c in cat_ids[:3]]
        source_exposure.append({
            "source": clean_for_html(source),
            "total": int(total or 0),
            "recent7d": int(recent7d or 0),
            "recent48": int(recent48 or 0),
            "categories": " / ".join(category_labels) if category_labels else "-",
        })

    # フィード健全性
    feed_issues = []
    try:
        cur.execute("""
            SELECT feed_url, failure_count, last_success_at, last_failure_reason
            FROM feed_health
            WHERE failure_count > 0
            ORDER BY failure_count DESC
        """)
        for url, fc, last_ok, reason in cur.fetchall():
            url_short = url if len(url) <= 50 else url[:47] + "..."
            feed_issues.append({
                "url": url,
                "url_short": url_short,
                "failure_count": int(fc or 0),
                "last_success": (last_ok or "")[:16],
                "reason": clean_for_html(reason or ""),
            })
    except Exception as e:
        # feed_healthテーブルが未作成のDBも許容するため debug レベル
        _log_render_error("ops.feed_health_query", e, level="debug")

    # 一次情報比率（kind制限なし）
    primary_ratio_threshold = float(os.environ.get("PRIMARY_RATIO_THRESHOLD", "0.5") or "0.5")
    cur.execute("""
        SELECT
          COALESCE(NULLIF(category,''), 'other') AS category,
          COUNT(*) AS total_count,
          SUM(CASE WHEN source_tier = 'primary' THEN 1 ELSE 0 END) AS primary_count,
          SUM(CASE WHEN source_tier IS NOT NULL AND source_tier != '' THEN 1 ELSE 0 END) AS tier_set_count
        FROM articles
        GROUP BY COALESCE(NULLIF(category,''), 'other')
        ORDER BY total_count DESC, category ASC
    """)
    primary_ratio_by_category = []
    for category, total_count, primary_count, tier_set_count in cur.fetchall():
        total_count = int(total_count or 0)
        primary_count = int(primary_count or 0)
        tier_set_count = int(tier_set_count or 0)
        if tier_set_count == 0:
            primary_ratio_by_category.append({
                "category": category, "total_count": total_count,
                "primary_count": 0, "ratio_pct": "-",
                "status": "na", "warn_reason": "",
            })
        else:
            ratio = (primary_count / total_count) if total_count else 0.0
            status = "ok" if ratio >= primary_ratio_threshold else "warn"
            primary_ratio_by_category.append({
                "category": category, "total_count": total_count,
                "primary_count": primary_count,
                "ratio_pct": round(ratio * 100, 1),
                "status": status,
                "warn_reason": "閾値未達" if status == "warn" else "",
            })

    # RSS数（sources.yamlから拾える範囲でカウント。取れなければ0）
    rss_sources = 0
    try:
        with open("src/sources.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cats = cfg.get("categories") or []
        if isinstance(cats, list):
            for c in cats:
                if isinstance(c, dict):
                    srcs = c.get("sources") or c.get("feeds") or []
                    if isinstance(srcs, list):
                        for s in srcs:
                            if isinstance(s, str) and s.startswith("http"):
                                rss_sources += 1
                            elif isinstance(s, dict) and isinstance(s.get("url"), str):
                                rss_sources += 1
        # fallback: top-level sources
        if rss_sources == 0:
            srcs = cfg.get("sources") or cfg.get("feeds") or []
            if isinstance(srcs, list):
                for s in srcs:
                    if isinstance(s, str) and s.startswith("http"):
                        rss_sources += 1
                    elif isinstance(s, dict) and isinstance(s.get("url"), str):
                        rss_sources += 1
    except Exception as e:
        _log_render_error("ops.rss_sources_count", e, level="warning")
        rss_sources = 0

    return {
        "ops_stats": ops_stats,
        "daily_trend": daily_trend,
        "category_dist": category_dist,
        "source_exposure": source_exposure,
        "feed_issues": feed_issues,
        "primary_ratio_by_category": primary_ratio_by_category,
        "primary_ratio_threshold": primary_ratio_threshold,
        "rss_sources": rss_sources,
    }


def _build_meta_and_jp_priority(cur, cutoff_48h: str, rss_sources: int) -> Dict[str, Any]:
    """上部サマリー用meta・JP優先トピックTOP10×2を取得・集計する（main() から分離）。"""
    runtime_sec = int(os.environ.get("RUNTIME_SEC", "0") or "0")

    # 記事総数（最終採用＝articlesテーブル件数）
    cur.execute("SELECT COUNT(*) FROM articles")
    total_articles = int(cur.fetchone()[0] or 0)

    # 新規記事数（48h）
    cur.execute(
        """
        SELECT COUNT(*)
        FROM articles
        WHERE datetime(COALESCE(NULLIF(published_at,''), fetched_at)) >= datetime(?)
        """,
        (cutoff_48h,),
    )
    new_articles_48h = int(cur.fetchone()[0] or 0)

    meta = {
        "generated_at_jst": None,  # 後で入れる
        "runtime_sec": runtime_sec,
        "total_articles": total_articles,
        "new_articles_48h": new_articles_48h,
        "rss_sources": rss_sources,
    }

    cur.execute(
        """
        SELECT
          t.id,
          COALESCE(t.title_ja, t.title) AS title,
          COALESCE(NULLIF(t.category,''), 'other') AS category,
          (
            SELECT a2.url
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY a2.id DESC
            LIMIT 1
          ) AS url,
          (
            SELECT COALESCE(
              NULLIF(a2.published_at,''),
              a2.fetched_at
            )
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY
              CASE
                WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                ELSE 1
              END,
              datetime(a2.fetched_at) DESC,
              datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
              a2.url ASC
            LIMIT 1
          ) AS article_date,
          (
            SELECT COALESCE(SUM(
              CASE
                WHEN datetime(
                  substr(
                    replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                    1, 19
                  )
                ) >= datetime(?) THEN 1
                ELSE 0
              END
            ), 0)
            FROM topic_articles ta3
            JOIN articles a3 ON a3.id = ta3.article_id
            WHERE ta3.topic_id = t.id
              AND COALESCE(NULLIF(a3.region, ''), 'global') = 'jp'
          ) AS recent,
          i.importance,
          i.summary,
          i.tags,
          i.perspectives,
          i.perspective_digest
        FROM topics t
        LEFT JOIN topic_insights i ON i.topic_id = t.id
        WHERE COALESCE(NULLIF(t.category,''), 'other') NOT IN ('news', 'market')
          AND NOT EXISTS (
            SELECT 1
            FROM topic_articles ta4
            JOIN articles a4 ON a4.id = ta4.article_id
            WHERE ta4.topic_id = t.id
              AND COALESCE(a4.kind,'') = 'news'
          )
          AND EXISTS (
            SELECT 1
            FROM topic_articles ta
            JOIN articles a ON a.id = ta.article_id
            WHERE ta.topic_id = t.id
              AND COALESCE(NULLIF(a.region, ''), 'global') = 'jp'
          )
        ORDER BY COALESCE(i.importance,0) DESC, COALESCE(recent,0) DESC, t.id ASC
        LIMIT 10
        """,
        (cutoff_48h,),
    )
    jp_priority_top = []
    for tid, title, category, url, article_date, recent, importance, summary, tags, perspectives, perspective_digest in cur.fetchall():
        jp_priority_top.append({
            "id": tid,
            "title": clean_for_html(title),
            "category": category,
            "url": url or "#",
            "recent": int(recent or 0),
            "importance": int(importance) if importance is not None else 0,
            "summary": summary or "",
            "tags": _safe_json_list(tags),
            "perspectives": _safe_json_obj(perspectives),
            "perspective_digest": _safe_json_obj(perspective_digest),
            "one_liner": "",
            "date": article_date or "",
        })

    cur.execute(
        """
        SELECT
          t.id,
          COALESCE(t.title_ja, t.title) AS title,
          COALESCE(NULLIF(t.category,''), 'other') AS category,
          (
            SELECT a2.url
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY a2.id DESC
            LIMIT 1
          ) AS url,
          (
            SELECT COALESCE(
              NULLIF(a2.published_at,''),
              a2.fetched_at
            )
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY
              CASE
                WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                ELSE 1
              END,
              datetime(a2.fetched_at) DESC,
              datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
              a2.url ASC
            LIMIT 1
          ) AS article_date,
          (
            SELECT COALESCE(SUM(
              CASE
                WHEN datetime(
                  substr(
                    replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                    1, 19
                  )
                ) >= datetime(?) THEN 1
                ELSE 0
              END
            ), 0)
            FROM topic_articles ta3
            JOIN articles a3 ON a3.id = ta3.article_id
            WHERE ta3.topic_id = t.id
              AND COALESCE(NULLIF(a3.region, ''), 'global') = 'jp'
          ) AS recent,
          i.importance,
          i.summary,
          i.tags,
          i.perspectives,
          i.perspective_digest
        FROM topics t
        LEFT JOIN topic_insights i ON i.topic_id = t.id
        WHERE COALESCE(NULLIF(t.category,''), 'other') NOT IN ('news', 'market')
          AND NOT EXISTS (
            SELECT 1
            FROM topic_articles ta4
            JOIN articles a4 ON a4.id = ta4.article_id
            WHERE ta4.topic_id = t.id
              AND COALESCE(a4.kind,'') = 'news'
          )
          AND (
            SELECT COALESCE(SUM(
              CASE
                WHEN datetime(
                  substr(
                    replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                    1, 19
                  )
                ) >= datetime(?) THEN 1
                ELSE 0
              END
            ), 0)
            FROM topic_articles ta3
            JOIN articles a3 ON a3.id = ta3.article_id
            WHERE ta3.topic_id = t.id
              AND COALESCE(NULLIF(a3.region, ''), 'global') = 'jp'
          ) > 0
        ORDER BY COALESCE(recent,0) DESC, COALESCE(i.importance,0) DESC, t.id ASC
        LIMIT 10
        """,
        (cutoff_48h, cutoff_48h),
    )
    jp_priority_trending_top = []
    for tid, title, category, url, article_date, recent, importance, summary, tags, perspectives, perspective_digest in cur.fetchall():
        jp_priority_trending_top.append({
            "id": tid,
            "title": clean_for_html(title),
            "category": category,
            "url": url or "#",
            "recent": int(recent or 0),
            "importance": int(importance) if importance is not None else 0,
            "summary": summary or "",
            "tags": _safe_json_list(tags),
            "perspectives": _safe_json_obj(perspectives),
            "perspective_digest": _safe_json_obj(perspective_digest),
            "one_liner": "",
            "date": article_date or "",
        })

    return {
        "meta": meta,
        "jp_priority_top": jp_priority_top,
        "jp_priority_trending_top": jp_priority_trending_top,
    }


def _build_tech_categories(cur) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    """カテゴリ一覧(YAML->DB->other)を構築し、tech側で使う tech_categories・cat_name を返す（main() から分離）。"""
    categories = load_categories_from_yaml()
    if not categories:
        categories = build_categories_fallback(cur)
    categories = ensure_category_coverage(cur, categories)

    category_order = [
        "system",
        "manufacturing",
        "smart_factory",
        "market",
        "environment",
        "quality",
        "maintenance",
        "policy",
        "decarbonization_ops",
        "security",
        "standards",
        "ai",
        "dev",
        "other",
    ]
    order_index = {cat_id: idx for idx, cat_id in enumerate(category_order)}
    # YAML定義から削除されたカテゴリ（DBに残存データがあっても非表示）
    allowed_cats = set(category_order)
    categories = [c for c in categories if c["id"] in allowed_cats]
    categories = sorted(
        categories,
        key=lambda c: (order_index.get(c["id"], len(category_order)), c["id"]),
    )

    TECH_EXCLUDE = {"news"}
    tech_categories = [c for c in categories if c["id"] not in TECH_EXCLUDE]

    # tech側で使うのは tech_categories
    cat_name = {c["id"]: c["name"] for c in tech_categories}
    return tech_categories, cat_name


def _build_tag_groups(topics_by_cat: Dict[str, List[Dict[str, Any]]], out_dir: Path) -> Dict[str, Any]:
    """topics_by_cat から insights_tech.json を書き出し、tag_list・tag_groups を返す（main() から分離）。"""
    # トップページ軽量化: details.insight の中身を insights_tech.json へ切り出す
    # （docs/index.html と docs/tech/index.html は同じ topics_by_cat から生成されるため1回だけ書き出せば両方から参照できる）
    insights_tech = {
        str(t["id"]): _insight_payload(t)
        for items in topics_by_cat.values()
        for t in items
        if t.get("has_insight")
    }
    write_insights_json(out_dir, "tech", insights_tech)

    all_tags = {}
    for cat_id, items in topics_by_cat.items():
        for t in items:
            for tg in (t.get("tags") or []):
                all_tags[tg] = all_tags.get(tg, 0) + 1
    tag_list = sorted(all_tags.items(), key=lambda x: (-x[1], x[0]))[:50]  # 上位50など

    # 改善4: タグをグループに分類
    TAG_GROUPS = {
        "技術": {"ai", "ml", "llm", "cloud", "api", "docker", "kubernetes", "devops", "cicd",
                 "database", "network", "linux", "python", "rust", "go", "java", "typescript",
                 "frontend", "backend", "performance", "compute", "gpu", "semiconductor", "hardware"},
        "セキュリティ": {"security", "vulnerability", "patch", "patch_window", "ransomware",
                         "authentication", "encryption", "privacy", "zero-day", "malware", "firewall"},
        "ビジネス": {"market", "investment", "regulation", "supply_chain", "price", "earnings",
                     "partnership", "strategy", "governance", "compliance"},
    }
    tag_dict = dict(tag_list)
    grouped: dict = {g: [] for g in TAG_GROUPS}
    grouped["その他"] = []
    for tg, cnt in tag_list:
        placed = False
        for g, members in TAG_GROUPS.items():
            if tg in members:
                grouped[g].append((tg, cnt))
                placed = True
                break
        if not placed:
            grouped["その他"].append((tg, cnt))
    tag_groups = [(g, tags) for g, tags in grouped.items() if tags]
    return {"tag_list": tag_list, "tag_groups": tag_groups}


def _build_category_topics(cur, cat_id: str, cutoff_48h: str, LIMIT_PER_CAT: int, HOT_TOP_N: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """1カテゴリ分の注目TOP5(hot_list)と一覧(items)を構築する（main() のループ本体を分離）。"""

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
                  WHEN datetime(
                    substr(
                      replace(replace(COALESCE(NULLIF(a.published_at,''), a.fetched_at),'T',' '),'+00:00',''),
                      1, 19
                    )
                  ) >= datetime(?) THEN 1
                  ELSE 0
                END
              ) AS recent_count,
              MAX(
                datetime(
                  substr(
                    replace(replace(COALESCE(NULLIF(a.published_at,''), a.fetched_at),'T',' '),'+00:00',''),
                    1, 19
                  )
                )
              ) AS article_date
            FROM topics t
            JOIN topic_articles ta ON ta.topic_id = t.id
            JOIN articles a ON a.id = ta.article_id
            WHERE (t.category IS NULL OR t.category = '')
              AND COALESCE(t.category,'') <> 'news'
              AND COALESCE(a.kind,'') <> 'news'
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
                  WHEN datetime(
                    substr(
                      replace(replace(COALESCE(NULLIF(a.published_at,''), a.fetched_at),'T',' '),'+00:00',''),
                      1, 19
                    )
                  ) >= datetime(?) THEN 1
                  ELSE 0
                END
              ) AS recent_count,
              MAX(
                datetime(
                  substr(
                    replace(replace(COALESCE(NULLIF(a.published_at,''), a.fetched_at),'T',' '),'+00:00',''),
                    1, 19
                  )
                )
              ) AS article_date
            FROM topics t
            JOIN topic_articles ta ON ta.topic_id = t.id
            JOIN articles a ON a.id = ta.article_id
            WHERE t.category = ?
              AND COALESCE(t.category,'') <> 'news'
              AND COALESCE(a.kind,'') <> 'news'
            GROUP BY t.id
            HAVING recent_count > 0
            ORDER BY recent_count DESC, total_count DESC, t.id DESC
            LIMIT ?
            """,
            (cutoff_48h, cat_id, HOT_TOP_N),
        )


    rows = cur.fetchall()
    hot_list = [
        {"id": tid, "title": clean_for_html(title), "articles": int(total), "recent": int(recent),"date": article_date or ""}
        for (tid, title, total, recent, article_date) in rows
    ]

    # ★ 注目TOP5の並びも完全決定（揺れ防止）
    hot_list = sorted(
        hot_list,
        key=lambda x: (-x["recent"], -x["articles"], x["id"]),
    )

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
                  ORDER BY
                      CASE
                        WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                        ELSE 1
                      END,
                      datetime(a2.fetched_at) DESC,
                      datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                      a2.url ASC
                    LIMIT 1

                ) AS url,
                (
                  SELECT COALESCE(
                    NULLIF(a2.published_at,''),
                    a2.fetched_at
                  )
                  FROM topic_articles ta2
                  JOIN articles a2 ON a2.id = ta2.article_id
                  WHERE ta2.topic_id = t.id
                  ORDER BY
                    CASE
                      WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                      ELSE 1
                    END,
                    datetime(a2.fetched_at) DESC,
                    datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                    a2.url ASC
                  LIMIT 1
                ) AS article_date,
              (
                  SELECT COALESCE(SUM(
                    CASE
                      WHEN datetime(
                        substr(
                          replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                          1, 19
                        )
                      ) >= datetime(?) THEN 1
                      ELSE 0
                    END
                  ), 0)
                  FROM topic_articles ta3
                  JOIN articles a3 ON a3.id = ta3.article_id
                  WHERE ta3.topic_id = t.id
                ) AS recent,
              (
                  SELECT a2.source
                  FROM topic_articles ta2
                  JOIN articles a2 ON a2.id = ta2.article_id
                  WHERE ta2.topic_id = t.id
                  ORDER BY
                      CASE WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0 ELSE 1 END,
                      datetime(a2.fetched_at) DESC,
                      datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                      a2.url ASC
                  LIMIT 1
                ) AS source,
              i.importance,
              i.summary,
              i.key_points,
              i.evidence_urls,
              i.tags,
              i.perspectives,
              i.perspective_digest
            FROM topics t
            LEFT JOIN topic_insights i ON i.topic_id = t.id
            WHERE (t.category IS NULL OR t.category = '')
              AND NOT EXISTS (
                SELECT 1
                FROM topic_articles ta4
                JOIN articles a4 ON a4.id = ta4.article_id
                WHERE ta4.topic_id = t.id
                  AND COALESCE(a4.kind,'') = 'news'
              )
            ORDER BY
              COALESCE(i.importance, 0) DESC,
              COALESCE(recent, 0) DESC,
              t.id DESC
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
                  ORDER BY
                      CASE
                        WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                        ELSE 1
                      END,
                      datetime(a2.fetched_at) DESC,
                      datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                      a2.url ASC
                    LIMIT 1
                ) AS url,
                (
                  SELECT COALESCE(
                    NULLIF(a2.published_at,''),
                    a2.fetched_at
                  )
                  FROM topic_articles ta2
                  JOIN articles a2 ON a2.id = ta2.article_id
                  WHERE ta2.topic_id = t.id
                  ORDER BY
                    CASE
                      WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                      ELSE 1
                    END,
                    datetime(a2.fetched_at) DESC,
                    datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                    a2.url ASC
                  LIMIT 1
                ) AS article_date,
              (
                  SELECT COALESCE(SUM(
                    CASE
                      WHEN datetime(
                        substr(
                          replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                          1, 19
                        )
                      ) >= datetime(?) THEN 1
                      ELSE 0
                    END
                  ), 0)
                  FROM topic_articles ta3
                  JOIN articles a3 ON a3.id = ta3.article_id
                  WHERE ta3.topic_id = t.id
                ) AS recent,
              (
                  SELECT a2.source
                  FROM topic_articles ta2
                  JOIN articles a2 ON a2.id = ta2.article_id
                  WHERE ta2.topic_id = t.id
                  ORDER BY
                      CASE WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0 ELSE 1 END,
                      datetime(a2.fetched_at) DESC,
                      datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                      a2.url ASC
                  LIMIT 1
                ) AS source,

              i.importance,
              i.summary,
              i.key_points,
              i.evidence_urls,
              i.tags,
              i.perspectives,
              i.perspective_digest
            FROM topics t
            LEFT JOIN topic_insights i ON i.topic_id = t.id
            WHERE t.category = ?
              AND COALESCE(t.category,'') <> 'news'
              AND NOT EXISTS (
                SELECT 1
                FROM topic_articles ta4
                JOIN articles a4 ON a4.id = ta4.article_id
                WHERE ta4.topic_id = t.id
                  AND COALESCE(a4.kind,'') = 'news'
              )
            ORDER BY
              COALESCE(i.importance, 0) DESC,
              COALESCE(recent, 0) DESC,
              t.id DESC
            LIMIT ?
            """,
            (cutoff_48h, cat_id, LIMIT_PER_CAT),
        )


    rows = cur.fetchall()
    items: List[Dict[str, Any]] = []
    for r in rows:
        tid, title, url, article_date, recent, source, importance, summary, key_points, evidence_urls, tags, perspectives, perspective_digest = r
        items.append(
            {
                "id": tid,
                "title": clean_for_html(title),  # ← ここはSQLで title_ja 優先済み
                "url": url or "#",
                "date": article_date or "",
                "recent": int(recent or 0),
                "source": source or "",
                "importance": int(importance) if importance is not None else None,
                "summary": summary or "",
                "key_points": _safe_json_list(key_points),
                "evidence_urls": _safe_json_list(evidence_urls),
                "tags": _safe_json_list(tags),
                "perspectives": _safe_json_obj(perspectives),
                "perspective_digest": _safe_json_obj(perspective_digest),
            }
        )
        items[-1]["has_insight"] = _tech_has_insight(items[-1])

    # トピック順を完全決定（最後の揺れ防止）
    items = sorted(
        items,
        key=lambda x: (
            -(x["importance"] or 0),
            -(x["recent"] or 0),
            x["id"]
        )
    )

    # ===== A: 確実対応：注目TOP5を詳細リストにも必ず混ぜる =====
    hot_ids = [x["id"] for x in hot_list]
    item_ids = {x["id"] for x in items}
    missing_ids = [tid for tid in hot_ids if tid not in item_ids]

    if missing_ids:
        # IN句プレースホルダを生成
        placeholders = ",".join(["?"] * len(missing_ids))

        if cat_id == "other":
            sql_missing = f"""
            SELECT
              t.id,
              COALESCE(t.title_ja, t.title) AS title,
              (
                  SELECT a2.url
                  FROM topic_articles ta2
                  JOIN articles a2 ON a2.id = ta2.article_id
                  WHERE ta2.topic_id = t.id
                  ORDER BY
                      CASE
                        WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                        ELSE 1
                      END,
                      datetime(a2.fetched_at) DESC,
                      datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                      a2.url ASC
                    LIMIT 1
                ) AS url,
                (
                  SELECT COALESCE(
                    NULLIF(a2.published_at,''),
                    a2.fetched_at
                  )
                  FROM topic_articles ta2
                  JOIN articles a2 ON a2.id = ta2.article_id
                  WHERE ta2.topic_id = t.id
                  ORDER BY
                    CASE
                      WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                      ELSE 1
                    END,
                    datetime(a2.fetched_at) DESC,
                    datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                    a2.url ASC
                  LIMIT 1
                ) AS article_date,
              (
                  SELECT COALESCE(SUM(
                    CASE
                      WHEN datetime(
                        substr(
                          replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                          1, 19
                        )
                      ) >= datetime(?) THEN 1
                      ELSE 0
                    END
                  ), 0)
                  FROM topic_articles ta3
                  JOIN articles a3 ON a3.id = ta3.article_id
                  WHERE ta3.topic_id = t.id
                ) AS recent,
              (
                  SELECT a2.source
                  FROM topic_articles ta2
                  JOIN articles a2 ON a2.id = ta2.article_id
                  WHERE ta2.topic_id = t.id
                  ORDER BY
                      CASE WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0 ELSE 1 END,
                      datetime(a2.fetched_at) DESC,
                      datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                      a2.url ASC
                  LIMIT 1
                ) AS source,
              i.importance,
              i.summary,
              i.key_points,
              i.evidence_urls,
              i.tags,
              i.perspectives,
              i.perspective_digest
            FROM topics t
            LEFT JOIN topic_insights i ON i.topic_id = t.id
            WHERE (t.category IS NULL OR t.category = '')
              AND COALESCE(NULLIF(t.category,''), 'other') NOT IN ('news', 'market')
              AND NOT EXISTS (
                SELECT 1
                FROM topic_articles ta4
                JOIN articles a4 ON a4.id = ta4.article_id
                WHERE ta4.topic_id = t.id
                  AND COALESCE(a4.kind,'') = 'news'
              )
              AND t.id IN ({placeholders})
            """
            params = [cutoff_48h, *missing_ids]
        else:
            sql_missing = f"""
            SELECT
              t.id,
              COALESCE(t.title_ja, t.title) AS title,
              (
                  SELECT a2.url
                  FROM topic_articles ta2
                  JOIN articles a2 ON a2.id = ta2.article_id
                  WHERE ta2.topic_id = t.id
                  ORDER BY
                      CASE
                        WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                        ELSE 1
                      END,
                      datetime(a2.fetched_at) DESC,
                      datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                      a2.url ASC
                    LIMIT 1
                ) AS url,
                (
                  SELECT COALESCE(
                    NULLIF(a2.published_at,''),
                    a2.fetched_at
                  )
                  FROM topic_articles ta2
                  JOIN articles a2 ON a2.id = ta2.article_id
                  WHERE ta2.topic_id = t.id
                  ORDER BY
                    CASE
                      WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                      ELSE 1
                    END,
                    datetime(a2.fetched_at) DESC,
                    datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                    a2.url ASC
                  LIMIT 1
                ) AS article_date,
              (
                  SELECT COALESCE(SUM(
                    CASE
                      WHEN datetime(
                        substr(
                          replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                          1, 19
                        )
                      ) >= datetime(?) THEN 1
                      ELSE 0
                    END
                  ), 0)
                  FROM topic_articles ta3
                  JOIN articles a3 ON a3.id = ta3.article_id
                  WHERE ta3.topic_id = t.id
                ) AS recent,
              (
                  SELECT a2.source
                  FROM topic_articles ta2
                  JOIN articles a2 ON a2.id = ta2.article_id
                  WHERE ta2.topic_id = t.id
                  ORDER BY
                      CASE WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0 ELSE 1 END,
                      datetime(a2.fetched_at) DESC,
                      datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                      a2.url ASC
                  LIMIT 1
                ) AS source,
              i.importance,
              i.summary,
              i.key_points,
              i.evidence_urls,
              i.tags,
              i.perspectives,
              i.perspective_digest
            FROM topics t
            LEFT JOIN topic_insights i ON i.topic_id = t.id
            WHERE t.category = ?
              AND COALESCE(NULLIF(t.category,''), 'other') <> 'news'
              AND NOT EXISTS (
                SELECT 1
                FROM topic_articles ta4
                JOIN articles a4 ON a4.id = ta4.article_id
                WHERE ta4.topic_id = t.id
                  AND COALESCE(a4.kind,'') = 'news'
              )
              AND t.id IN ({placeholders})
            """
            params = [cutoff_48h, cat_id, *missing_ids]

        cur.execute(sql_missing, params)
        for r in cur.fetchall():
            tid, title, url, article_date, recent, source, importance, summary, key_points, evidence_urls, tags, perspectives, perspective_digest = r
            items.append(
                {
                    "id": tid,
                    "title": clean_for_html(title),
                    "url": url or "#",
                    "date": article_date or "",
                    "recent": int(recent or 0),
                    "source": source or "",
                    "importance": int(importance) if importance is not None else None,
                    "summary": summary or "",
                    "key_points": _safe_json_list(key_points),
                    "evidence_urls": _safe_json_list(evidence_urls),
                    "tags": _safe_json_list(tags),
                    "perspectives": _safe_json_obj(perspectives),
                    "perspective_digest": _safe_json_obj(perspective_digest),
                }
            )

        # 再ソート（表示順の規則を維持）
        items = sorted(
            items,
            key=lambda x: (
                -(x["importance"] or 0),
                -(x["recent"] or 0),
                x["id"]
            )
        )

        # 表示件数を LIMIT_PER_CAT に戻す（ただし注目TOP5は落とさない）
        hot_set = set(hot_ids)
        kept = []
        for it in items:
            if len(kept) >= LIMIT_PER_CAT and it["id"] not in hot_set:
                continue
            kept.append(it)
        items = kept
    # ===== A: 確実対応ここまで =====


    # 改善3: importance が None かつ recent=0 の記事を除外（ファイルサイズ最適化）
    hot_set_for_filter = {x["id"] for x in hot_list}
    items = [
        it for it in items
        if it["importance"] is not None or it["recent"] > 0 or it["id"] in hot_set_for_filter
    ]

    return hot_list, items


def main():
    t0 = _now_sec()
    print("[TIME] step=render start")

    out_dir = Path("docs")
    out_dir.mkdir(exist_ok=True)

    conn = connect()
    cur = conn.cursor()
    # categories: YAML -> DB -> other
    tech_categories, cat_name = _build_tech_categories(cur)

    topics_by_cat: Dict[str, List[Dict[str, Any]]] = {}
    hot_by_cat: Dict[str, List[Dict[str, Any]]] = {}
    # 48h cutoff（UTCでSQLite互換の "YYYY-MM-DD HH:MM:SS"）
    cutoff_48h = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")

    LIMIT_PER_CAT = 15
    HOT_TOP_N = 5

    for cat in tech_categories:
        cat_id = cat["id"]
        hot_list, items = _build_category_topics(cur, cat_id, cutoff_48h, LIMIT_PER_CAT, HOT_TOP_N)
        hot_by_cat[cat_id] = hot_list
        topics_by_cat[cat_id] = items

    _tag_data = _build_tag_groups(topics_by_cat, out_dir)
    tag_list = _tag_data["tag_list"]
    tag_groups = _tag_data["tag_groups"]
    # --- ops用データ取得 ---
    _ops_data = _build_ops_page_data(cur, cutoff_48h, cat_name)
    ops_stats = _ops_data["ops_stats"]
    daily_trend = _ops_data["daily_trend"]
    category_dist = _ops_data["category_dist"]
    source_exposure = _ops_data["source_exposure"]
    feed_issues = _ops_data["feed_issues"]
    primary_ratio_by_category = _ops_data["primary_ratio_by_category"]
    primary_ratio_threshold = _ops_data["primary_ratio_threshold"]
    rss_sources = _ops_data["rss_sources"]

    # --- UX改善①: 上部サマリー用meta・JP優先トピックTOP10×2 ---
    _meta_data = _build_meta_and_jp_priority(cur, cutoff_48h, rss_sources)
    meta = _meta_data["meta"]
    jp_priority_top = _meta_data["jp_priority_top"]
    jp_priority_trending_top = _meta_data["jp_priority_trending_top"]

    # --- UX改善①: カテゴリ横断TOP ---
    # Global Top 10: importance desc, recent desc, id asc（完全決定）
    TECH_CATS = {"ai", "dev", "security", "system", "manufacturing", "cloud", "data"}  # 必要に応じて調整
    tech_cat_ids = [c["id"] for c in tech_categories if c.get("id") in TECH_CATS]
    if not tech_cat_ids:
        tech_cat_ids = [c["id"] for c in tech_categories if c.get("id") and c.get("id") != "other"]

    ph = ",".join(["?"] * len(tech_cat_ids))
    cur.execute(
    f"""
    SELECT
      t.id,
      COALESCE(t.title_ja, t.title) AS title,
      COALESCE(NULLIF(t.category,''), 'other') AS category,
      (
        SELECT a2.url
        FROM topic_articles ta2
        JOIN articles a2 ON a2.id = ta2.article_id
        WHERE ta2.topic_id = t.id
        ORDER BY a2.id DESC
        LIMIT 1
      ) AS url,
      (
        SELECT COALESCE(
          NULLIF(a2.published_at,''),
          a2.fetched_at
        )
        FROM topic_articles ta2
        JOIN articles a2 ON a2.id = ta2.article_id
        WHERE ta2.topic_id = t.id
        ORDER BY
          CASE
            WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
            ELSE 1
          END,
          datetime(a2.fetched_at) DESC,
          datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
          a2.url ASC
        LIMIT 1
      ) AS article_date,
      (
        SELECT COALESCE(SUM(
          CASE
            WHEN datetime(
              substr(
                replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                1, 19
              )
            ) >= datetime(?) THEN 1
            ELSE 0
          END
        ), 0)
        FROM topic_articles ta3
        JOIN articles a3 ON a3.id = ta3.article_id
        WHERE ta3.topic_id = t.id
      ) AS recent,
      i.importance,
      i.summary,
      i.tags,
      i.perspectives,
      i.perspective_digest
    FROM topics t
    LEFT JOIN topic_insights i ON i.topic_id = t.id
    WHERE COALESCE(NULLIF(t.category,''), 'other') NOT IN ('news', 'market')
      AND NOT EXISTS (
        SELECT 1
        FROM topic_articles ta4
        JOIN articles a4 ON a4.id = ta4.article_id
        WHERE ta4.topic_id = t.id
          AND COALESCE(a4.kind,'') = 'news'
      )
    ORDER BY
      CASE
        WHEN COALESCE(NULLIF(t.category,''), 'other') IN ({ph}) THEN 1
        ELSE 0
      END DESC,
      COALESCE(i.importance,0) DESC,
      COALESCE(recent,0) DESC,
      t.id ASC
    LIMIT 10
    """,
    (cutoff_48h, *tech_cat_ids),
)

    global_top = []
    for tid, title, category, url, article_date,recent, importance, summary, tags, perspectives, perspective_digest in cur.fetchall():
        global_top.append({
            "id": tid,
            "title": clean_for_html(title),
            "category": category,   # ★追加
            "url": url or "#",
            "recent": int(recent or 0),
            "importance": int(importance) if importance is not None else 0,
            "summary": summary or "",
            "tags": _safe_json_list(tags),
            "perspectives": _safe_json_obj(perspectives),
            "perspective_digest": _safe_json_obj(perspective_digest),
            "one_liner": "",  # 今は空でOK（後で短文化したければ追加）
            "date": article_date or "",
        })

    # Trending Top 10: recent desc, importance desc, id asc（完全決定）
    cur.execute(
        """
        SELECT
          t.id,
          COALESCE(t.title_ja, t.title) AS title,
          COALESCE(NULLIF(t.category,''), 'other') AS category,
          (
            SELECT a2.url
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY a2.id DESC
            LIMIT 1
          ) AS url,
          (
            SELECT COALESCE(
              NULLIF(a2.published_at,''),
              a2.fetched_at
            )
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY
              CASE
                WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                ELSE 1
              END,
              datetime(a2.fetched_at) DESC,
              datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
              a2.url ASC
            LIMIT 1
          ) AS article_date,
          (
            SELECT COALESCE(SUM(
              CASE
                WHEN datetime(
                  substr(
                    replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                    1, 19
                  )
                ) >= datetime(?) THEN 1
              END
            ), 0)
            FROM topic_articles ta3
            JOIN articles a3 ON a3.id = ta3.article_id
            WHERE ta3.topic_id = t.id
          ) AS recent,
          i.importance,
          i.summary,
          i.tags,
          i.perspectives,
          i.perspective_digest
        FROM topics t
        LEFT JOIN topic_insights i ON i.topic_id = t.id
        WHERE (
          SELECT COALESCE(SUM(
            CASE
              WHEN datetime(
                substr(
                  replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                  1, 19
                )
              ) >= datetime(?) THEN 1
              ELSE 0
            END
          ), 0)
          FROM topic_articles ta3
          JOIN articles a3 ON a3.id = ta3.article_id
          WHERE ta3.topic_id = t.id
        ) > 0
          AND COALESCE(NULLIF(t.category,''), 'other') NOT IN ('news', 'market')
          AND NOT EXISTS (
            SELECT 1
            FROM topic_articles ta4
            JOIN articles a4 ON a4.id = ta4.article_id
            WHERE ta4.topic_id = t.id
              AND COALESCE(a4.kind,'') = 'news'
          )
        ORDER BY COALESCE(recent,0) DESC, COALESCE(i.importance,0) DESC, t.id ASC
        LIMIT 10
        """,
        (cutoff_48h, cutoff_48h),
    )
    trending_top = []
    for tid, title, category, url, article_date, recent, importance, summary, tags, perspectives, perspective_digest in cur.fetchall():
        trending_top.append({
            "id": tid,
            "title": clean_for_html(title),
            "category": category,   # ★追加
            "url": url or "#",
            "recent": int(recent or 0),
            "importance": int(importance) if importance is not None else 0,
            "summary": summary or "",
            "tags": _safe_json_list(tags),
            "perspectives": _safe_json_obj(perspectives),
            "perspective_digest": _safe_json_obj(perspective_digest),
            "one_liner": "",
            "date": article_date or "",
        })

    cur.execute(
        """
        SELECT
          t.id,
          COALESCE(t.title_ja, t.title) AS title,
          COALESCE(NULLIF(t.category,''), 'other') AS category,
          (
            SELECT a2.url
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY a2.id DESC
            LIMIT 1
          ) AS url,
          (
            SELECT COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC
            LIMIT 1
          ) AS article_date,
          (
            SELECT COALESCE(SUM(
              CASE
                WHEN datetime(
                  substr(
                    replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                    1, 19
                  )
                ) >= datetime(?) THEN 1
                ELSE 0
              END
            ), 0)
            FROM topic_articles ta3
            JOIN articles a3 ON a3.id = ta3.article_id
            WHERE ta3.topic_id = t.id
          ) AS recent,
          i.importance,
          i.summary,
          i.tags,
          i.perspectives,
          i.perspective_digest
        FROM topics t
        LEFT JOIN topic_insights i ON i.topic_id = t.id
        WHERE COALESCE(NULLIF(t.category,''), 'other') = 'market'
        ORDER BY COALESCE(i.importance,0) DESC, COALESCE(recent,0) DESC, t.id ASC
        LIMIT 10
        """,
        (cutoff_48h,),
    )
    market_top = []
    for tid, title, category, url, article_date, recent, importance, summary, tags, perspectives, perspective_digest in cur.fetchall():
        market_top.append({
            "id": tid,
            "title": clean_for_html(title),
            "category": category,
            "url": url or "#",
            "recent": int(recent or 0),
            "importance": int(importance) if importance is not None else 0,
            "summary": summary or "",
            "tags": _safe_json_list(tags),
            "perspectives": _safe_json_obj(perspectives),
            "perspective_digest": _safe_json_obj(perspective_digest),
            "one_liner": "",
            "date": article_date or "",
        })

    cur.execute(
        """
        SELECT
          t.id,
          COALESCE(t.title_ja, t.title) AS title,
          COALESCE(NULLIF(t.category,''), 'other') AS category,
          (
            SELECT a2.url
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY a2.id DESC
            LIMIT 1
          ) AS url,
          (
            SELECT COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC
            LIMIT 1
          ) AS article_date,
          (
            SELECT COALESCE(SUM(
              CASE
                WHEN datetime(
                  substr(
                    replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                    1, 19
                  )
                ) >= datetime(?) THEN 1
                ELSE 0
              END
            ), 0)
            FROM topic_articles ta3
            JOIN articles a3 ON a3.id = ta3.article_id
            WHERE ta3.topic_id = t.id
          ) AS recent,
          i.importance,
          i.summary,
          i.tags,
          i.perspectives,
          i.perspective_digest
        FROM topics t
        LEFT JOIN topic_insights i ON i.topic_id = t.id
        WHERE COALESCE(NULLIF(t.category,''), 'other') = 'market'
          AND (
            SELECT COALESCE(SUM(
              CASE
                WHEN datetime(
                  substr(
                    replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                    1, 19
                  )
                ) >= datetime(?) THEN 1
                ELSE 0
              END
            ), 0)
            FROM topic_articles ta3
            JOIN articles a3 ON a3.id = ta3.article_id
            WHERE ta3.topic_id = t.id
          ) > 0
        ORDER BY COALESCE(recent,0) DESC, COALESCE(i.importance,0) DESC, t.id ASC
        LIMIT 10
        """,
        (cutoff_48h, cutoff_48h),
    )
    market_trending_top = []
    for tid, title, category, url, article_date, recent, importance, summary, tags, perspectives, perspective_digest in cur.fetchall():
        market_trending_top.append({
            "id": tid,
            "title": clean_for_html(title),
            "category": category,
            "url": url or "#",
            "recent": int(recent or 0),
            "importance": int(importance) if importance is not None else 0,
            "summary": summary or "",
            "tags": _safe_json_list(tags),
            "perspectives": _safe_json_obj(perspectives),
            "perspective_digest": _safe_json_obj(perspective_digest),
            "one_liner": "",
            "date": article_date or "",
        })

    # 改善5: テック→ニュース相互リンク用マップ
    NEWS_LINK_MAP = {
        "manufacturing": ("manufacturing", "製造業ニュース"),
        "security": ("security", "セキュリティニュース"),
        "system": ("policy", "政策・規制ニュース"),
        "dev": ("company", "企業動向ニュース"),
    }

    # 生成日時（JST）
    generated_at = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S JST")
    meta["generated_at_jst"] = generated_at

    tech_dir = out_dir / "tech"
    tech_dir.mkdir(exist_ok=True)

    tech_sub_assets = build_asset_paths()
    tech_root_assets = build_asset_paths()

    tech_html_sub = _jinja_env.get_template("tech.html").render(
        common_css_href=tech_sub_assets["common_css_href"],
        common_js_src=tech_sub_assets["common_js_src"],
        page="tech",
        nav_prefix=tech_sub_assets["nav_prefix"],
        categories=tech_categories,
        cat_name=cat_name,
        topics_by_cat=topics_by_cat,
        hot_by_cat=hot_by_cat,
        generated_at=generated_at,
        meta=meta,
        jp_priority_top=jp_priority_top,
        jp_priority_trending_top=jp_priority_trending_top,
        global_top=global_top,
        trending_top=trending_top,
        market_top=market_top,
        market_trending_top=market_trending_top,
        tag_list=tag_list,
        tag_groups=tag_groups,
        news_link_map=NEWS_LINK_MAP,
        source_exposure=source_exposure,
        primary_ratio_by_category=primary_ratio_by_category,
        primary_ratio_threshold=primary_ratio_threshold,
        fmt_date=fmt_date,
    )

    tech_html_root = _jinja_env.get_template("tech.html").render(
        common_css_href=tech_root_assets["common_css_href"],
        common_js_src=tech_root_assets["common_js_src"],
        page="tech",
        nav_prefix=tech_root_assets["nav_prefix"],
        categories=tech_categories,
        cat_name=cat_name,
        topics_by_cat=topics_by_cat,
        hot_by_cat=hot_by_cat,
        generated_at=generated_at,
        meta=meta,
        jp_priority_top=jp_priority_top,
        jp_priority_trending_top=jp_priority_trending_top,
        global_top=global_top,
        trending_top=trending_top,
        market_top=market_top,
        market_trending_top=market_trending_top,
        tag_list=tag_list,
        tag_groups=tag_groups,
        news_link_map=NEWS_LINK_MAP,
        source_exposure=source_exposure,
        primary_ratio_by_category=primary_ratio_by_category,
        primary_ratio_threshold=primary_ratio_threshold,
        fmt_date=fmt_date,
    )

    # グローバル _render_errors を利用（モジュール先頭で定義）。
    # main() 呼び出しの冪等性のため、ここでクリアする。
    _render_errors.clear()

    try:
        (tech_dir / "index.html").write_text(tech_html_sub, encoding="utf-8")
        (out_dir / "index.html").write_text(tech_html_root, encoding="utf-8")
    except Exception as e:
        _log_render_error("tech.write_html", e, level="error")

    try:
        ops_dir = out_dir / "ops"
        ops_dir.mkdir(exist_ok=True)
        ops_assets = build_asset_paths()
        # フィード品質スコア（feed_quality モジュール）
        try:
            from feed_quality import compute_feed_quality
            feed_quality = compute_feed_quality(cur)
        except Exception as _fqe:
            _log_render_error("ops.feed_quality", _fqe, level="warning")
            feed_quality = []
        ops_html = _jinja_env.get_template("ops.html").render(
            common_css_href=ops_assets["common_css_href"],
            common_js_src=ops_assets["common_js_src"],
            page="ops",
            nav_prefix=ops_assets["nav_prefix"],
            meta=meta,
            cat_name=cat_name,
            ops=ops_stats,
            daily_trend=daily_trend,
            category_dist=category_dist,
            source_exposure=source_exposure,
            feed_issues=feed_issues,
            feed_quality=feed_quality,
            primary_ratio_by_category=primary_ratio_by_category,
            primary_ratio_threshold=primary_ratio_threshold,
        )
        (ops_dir / "index.html").write_text(ops_html, encoding="utf-8")
    except Exception as e:
        _log_render_error("ops.render", e, level="error")

    try:
        render_news_pages(out_dir, generated_at, cur)
    except Exception as e:
        _log_render_error("news.render", e, level="error")

    try:
        render_forecast_page(out_dir, generated_at, cur)
    except Exception as e:
        _log_render_error("forecast.render", e, level="error")

    try:
        render_forecast_hits_page(out_dir, generated_at, cur)
    except Exception as e:
        _log_render_error("forecast_hits.render", e, level="error")

    try:
        render_rss_feed(out_dir, generated_at, cur)
    except Exception as e:
        _log_render_error("feed.render", e, level="error")

    try:
        render_search_page(out_dir, generated_at, cur)
    except Exception as e:
        _log_render_error("search.render", e, level="error")

    try:
        # Diff ビュー生成（スナップショット保存→差分描画）
        from diff_view import save_today_snapshot, render_diff_page
        save_today_snapshot(conn)
        render_diff_page(out_dir, conn)
    except Exception as e:
        _log_render_error("diff.render", e, level="error")

    try:
        # トピックタイムライン。生成済みメインページが 📈 経緯リンクで参照している
        # トピック ID をすべて含める（リンク切れ防止のため HTML から実リンクを回収）
        import re as _re
        from topic_timeline import render_topic_timelines
        linked_ids: set[int] = set()
        for _page in ("index.html", "tech/index.html", "news/index.html"):
            _p = out_dir / _page
            if _p.exists():
                linked_ids |= {
                    int(m) for m in _re.findall(
                        r'href="/daily-tech-trend/topic/(\d+)/"', _p.read_text(encoding="utf-8")
                    )
                }
        render_topic_timelines(out_dir, top_n=50, include_ids=linked_ids, conn=conn)
    except Exception as e:
        _log_render_error("topic_timeline.render", e, level="error")

    try:
        # エンティティ抽出＋企業別ページ（辞書マッチ）
        from entities import extract_entities_by_dict, render_entity_pages
        extract_entities_by_dict(conn)
        render_entity_pages(out_dir, conn=conn, top_n=30)
    except Exception as e:
        _log_render_error("entities.render", e, level="error")

    try:
        # sitemap は diff ページ生成後に作成（リンクを含めるため）
        render_sitemap(out_dir, cur)
    except Exception as e:
        _log_render_error("sitemap.render", e, level="error")

    try:
        render_json_api(out_dir, cur)
    except Exception as e:
        _log_render_error("api.render", e, level="error")

    conn.close()

    if _render_errors:
        _logger.warning(
            "render completed with %d error(s): %s",
            len(_render_errors),
            _render_errors,
        )
    print(f"[TIME] step=render end sec={_now_sec() - t0:.1f}")

if __name__ == "__main__":
    main()
