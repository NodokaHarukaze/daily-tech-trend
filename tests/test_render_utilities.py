"""render_main の軽量ユーティリティ関数に対する単体テスト。

5091 行の render_main.py のうち、純関数として動作する低レベルヘルパーの
リグレッションを検知する。RSS 生成・検索ページ生成も in-memory SQLite で
動作確認する。
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

import render_main


# --- _safe_json_list / _safe_json_obj ---------------------------------------
def test_safe_json_list_returns_list_for_valid_array():
    assert render_main._safe_json_list('["a","b","c"]') == ["a", "b", "c"]


def test_safe_json_list_returns_empty_for_malformed():
    assert render_main._safe_json_list('not json') == []


def test_safe_json_list_returns_empty_for_none_and_empty():
    assert render_main._safe_json_list(None) == []
    assert render_main._safe_json_list("") == []


def test_safe_json_obj_returns_dict_for_valid():
    assert render_main._safe_json_obj('{"a":1}') == {"a": 1}


def test_safe_json_obj_returns_empty_for_malformed():
    assert render_main._safe_json_obj("not json") == {}


def test_safe_json_obj_returns_empty_for_array_input():
    # 配列を渡した場合は dict ではないので {} を返す
    assert render_main._safe_json_obj('[1,2,3]') == {}


# --- fmt_date --------------------------------------------------------------
def test_fmt_date_jst_conversion():
    result = render_main.fmt_date("2026-04-18T00:00:00+00:00")
    # UTC 00:00 → JST 09:00
    assert "09:00" in result
    assert "2026" in result


def test_fmt_date_invalid_returns_first_16_chars():
    assert render_main.fmt_date("garbage") == "garbage"
    assert render_main.fmt_date("") == ""


# --- _extract_domain -------------------------------------------------------
def test_extract_domain_normalizes_case():
    assert render_main._extract_domain("HTTPS://Example.COM/path") == "example.com"


def test_extract_domain_empty_on_invalid():
    assert render_main._extract_domain("") == ""


# --- _rss_escape / _rss_rfc822 --------------------------------------------
def test_rss_escape_escapes_xml_special_chars():
    assert render_main._rss_escape('<a href="x">&\'</a>') == (
        "&lt;a href=&quot;x&quot;&gt;&amp;'&lt;/a&gt;"
    )


def test_rss_rfc822_parses_iso8601():
    out = render_main._rss_rfc822("2026-04-18T12:34:56+00:00")
    assert "Sat, 18 Apr 2026 12:34:56 +0000" == out


def test_rss_rfc822_falls_back_for_malformed():
    out = render_main._rss_rfc822("not a date")
    # フォールバック: 現在時刻が RFC822 形式で返る
    assert "+0000" in out
    assert len(out) > 20


# --- render_rss_feed integration ------------------------------------------
def _bootstrap_minimal_db(tmp_path: Path) -> sqlite3.Connection:
    """最小限のテーブルを作った in-memory DB をセットアップする。"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE articles(id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
        "title_ja TEXT, category TEXT, published_at TEXT, fetched_at TEXT, "
        "source TEXT, content TEXT, kind TEXT)"
    )
    cur.execute(
        "CREATE TABLE topics(id INTEGER PRIMARY KEY, title TEXT, title_ja TEXT, "
        "category TEXT, created_at TEXT)"
    )
    cur.execute(
        "CREATE TABLE topic_articles(topic_id INTEGER, article_id INTEGER, "
        "is_representative INTEGER DEFAULT 0)"
    )
    cur.execute(
        "CREATE TABLE topic_insights(topic_id INTEGER PRIMARY KEY, summary TEXT, "
        "updated_at TEXT)"
    )
    cur.execute(
        "INSERT INTO articles VALUES(1,'https://ex.com/a','T1','タイトル1','ai',"
        "'2026-04-18T00:00:00+00:00','2026-04-18','src','本文','tech')"
    )
    cur.execute(
        "INSERT INTO topics VALUES(1,'T1','タイトル1','ai','2026-04-18T00:00:00+00:00')"
    )
    cur.execute("INSERT INTO topic_articles VALUES(1,1,1)")
    cur.execute("INSERT INTO topic_insights VALUES(1,'要約テスト','2026-04-18T00:00:00+00:00')")
    conn.commit()
    return conn


def test_render_rss_feed_writes_valid_xml(tmp_path: Path):
    conn = _bootstrap_minimal_db(tmp_path)
    render_main.render_rss_feed(
        tmp_path, "2026-04-18T00:00:00+00:00", conn.cursor(), limit=10
    )
    feed_path = tmp_path / "feed.xml"
    assert feed_path.exists()
    content = feed_path.read_text(encoding="utf-8")
    assert content.startswith('<?xml version="1.0"')
    assert "<rss version=\"2.0\"" in content
    assert "<title>タイトル1</title>" in content
    assert "<pubDate>" in content


def test_render_search_page_writes_json_and_html(tmp_path: Path):
    conn = _bootstrap_minimal_db(tmp_path)
    render_main.render_search_page(
        tmp_path, "2026-04-18T00:00:00+00:00", conn.cursor(), limit=10
    )
    assert (tmp_path / "search-index.json").exists()
    assert (tmp_path / "search.html").exists()
    import json

    data = json.loads((tmp_path / "search-index.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["u"] == "https://ex.com/a"
    assert data[0]["tj"] == "タイトル1"


# --- forecast 空殻プレースホルダ救済 -----------------------------------------
def _bootstrap_forecast_db(tmp_path: Path, md_text: str) -> sqlite3.Connection:
    """forecast_reports 1件入りのテスト用DBを作成。"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE forecast_reports(report_date TEXT PRIMARY KEY, file_path TEXT, "
        "executive_summary TEXT, accuracy_score REAL)"
    )
    cur.execute(
        "CREATE TABLE forecast_verifications(report_date TEXT, horizon TEXT, "
        "verification_round INTEGER, accuracy_score REAL)"
    )
    md_path = tmp_path / "report_2026-04-20.md"
    md_path.write_text(md_text, encoding="utf-8")
    cur.execute(
        "INSERT INTO forecast_reports VALUES('2026-04-20', ?, 'summary', NULL)",
        (str(md_path),),
    )
    conn.commit()
    return conn


_BROKEN_FORECAST_MD = """# 多視点ニュース分析・未来予測レポート

**生成日時:** 2026-04-20 12:00:00

---

# 今週の最重要ポイント

1. **テストポイント**
   *あなたへの影響:* 何か

---

# 未来予測

## 1週間後

### 1. 予測1

**影響度: 中 / 確信度: 中**

- **予測内容**：

> **根拠**:

## 1〜6ヶ月後

### 1. 実予測

**影響度: 大 / 確信度: 高**

- **予測内容**：これは本物の予測内容です

> **根拠**: 参考ニュース

## 1年後

### 1. 予測1

**影響度: 中 / 確信度: 中**

- **予測内容**：

> **根拠**:
"""


# --- perspective_digest（立場別くわしい解説）------------------------------
def _bootstrap_news_db_with_perspective_digest() -> sqlite3.Connection:
    """news 記事1件 + topic_insights（perspectives / perspective_digest 両方入り）を
    持つ in-memory DB を作る。"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE articles(id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
        "title_ja TEXT, category TEXT, region TEXT, published_at TEXT, "
        "fetched_at TEXT, source TEXT, content TEXT, kind TEXT)"
    )
    cur.execute(
        "CREATE TABLE topics(id INTEGER PRIMARY KEY, title TEXT, title_ja TEXT, "
        "category TEXT, created_at TEXT)"
    )
    cur.execute(
        "CREATE TABLE topic_articles(topic_id INTEGER, article_id INTEGER, "
        "is_representative INTEGER DEFAULT 0)"
    )
    cur.execute(
        "CREATE TABLE topic_insights(topic_id INTEGER PRIMARY KEY, importance INTEGER, "
        "type TEXT, summary TEXT, key_points TEXT, evidence_urls TEXT, tags TEXT, "
        "perspectives TEXT, perspective_digest TEXT, updated_at TEXT)"
    )
    cur.execute(
        "INSERT INTO articles VALUES(1,'https://ex.com/a','T1','タイトル1','news','jp',"
        "'2026-04-18T00:00:00+00:00','2026-04-18','src','本文','news')"
    )
    cur.execute(
        "INSERT INTO topics VALUES(1,'T1','タイトル1','news','2026-04-18T00:00:00+00:00')"
    )
    cur.execute("INSERT INTO topic_articles VALUES(1,1,1)")
    import json as _json

    cur.execute(
        "INSERT INTO topic_insights VALUES(1,5,'analysis','要約テスト','[]','[]','[]',?,?,"
        "'2026-04-18T00:00:00+00:00')",
        (
            _json.dumps({"engineer": "技術者短評"}),
            _json.dumps({"engineer": "技術者向けのくわしい解説文" * 5}),
        ),
    )
    conn.commit()
    return conn


def test_fetch_news_articles_by_category_includes_perspective_digest():
    conn = _bootstrap_news_db_with_perspective_digest()
    rows = render_main.fetch_news_articles_by_category(conn.cursor(), "jp", "news", limit=10)
    assert len(rows) == 1
    # perspectives の直後に perspective_digest が並ぶ（SELECT句の並び順）
    row = rows[0]
    assert render_main._safe_json_obj(row[12]) == {"engineer": "技術者短評"}
    assert render_main._safe_json_obj(row[13])["engineer"].startswith("技術者向けのくわしい解説文")


def test_render_news_region_page_item_has_perspective_digest():
    conn = _bootstrap_news_db_with_perspective_digest()
    sections = render_main.render_news_region_page(conn.cursor(), "jp", limit_each=10)
    news_section = next(s for s in sections if s["count"] > 0)
    item = (news_section["rows"] or news_section["other_rows"])[0]
    assert item["perspectives"] == {"engineer": "技術者短評"}
    assert item["perspective_digest"]["engineer"].startswith("技術者向けのくわしい解説文")


def _render_news_html(sections) -> str:
    news_assets = render_main.build_asset_paths()
    return render_main._jinja_env.get_template("news.html").render(
        common_css_href=news_assets["common_css_href"],
        common_js_src=news_assets["common_js_src"],
        page="news",
        nav_prefix=news_assets["nav_prefix"],
        title="ニュースダイジェスト",
        heading="ニュースダイジェスト",
        generated_at="2026-04-18T00:00:00+00:00",
        meta={"generated_at_jst": "", "total_articles": 1, "new_articles_48h": 0, "jp_count": 1, "global_count": 0},
        tag_list=[],
        sections=sections,
    )


def test_news_html_uses_lazy_placeholder_instead_of_inline_perspective_digest():
    """トップページ軽量化（2026-07-28実装）以降、perspectives/perspective_digestの実データは
    HTMLへ直接出力せず insights_news.json 側に切り出し、HTML側は data-insight-topic 付きの
    プレースホルダのみを出力する。"""
    conn = _bootstrap_news_db_with_perspective_digest()
    sections = render_main.render_news_region_page(conn.cursor(), "jp", limit_each=10)
    html = _render_news_html(sections)

    assert "技術者短評" not in html
    assert "立場別くわしい解説" not in html
    assert 'data-insight-topic="1"' in html
    assert "読み込み中" in html

    # 実データ自体は render_news_region_page の戻り値（insights_news.json 書き出し元）に残る
    news_section = next(s for s in sections if s["count"] > 0)
    item = (news_section["rows"] or news_section["other_rows"])[0]
    payload = render_main._insight_payload(item)
    assert payload["perspectives"] == {"engineer": "技術者短評"}
    assert payload["perspective_digest"]["engineer"].startswith("技術者向けのくわしい解説文")


def test_news_html_hides_perspective_digest_when_empty():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE articles(id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
        "title_ja TEXT, category TEXT, region TEXT, published_at TEXT, "
        "fetched_at TEXT, source TEXT, content TEXT, kind TEXT)"
    )
    cur.execute(
        "CREATE TABLE topics(id INTEGER PRIMARY KEY, title TEXT, title_ja TEXT, "
        "category TEXT, created_at TEXT)"
    )
    cur.execute(
        "CREATE TABLE topic_articles(topic_id INTEGER, article_id INTEGER, "
        "is_representative INTEGER DEFAULT 0)"
    )
    cur.execute(
        "CREATE TABLE topic_insights(topic_id INTEGER PRIMARY KEY, importance INTEGER, "
        "type TEXT, summary TEXT, key_points TEXT, evidence_urls TEXT, tags TEXT, "
        "perspectives TEXT, perspective_digest TEXT, updated_at TEXT)"
    )
    cur.execute(
        "INSERT INTO articles VALUES(1,'https://ex.com/a','T1','タイトル1','news','jp',"
        "'2026-04-18T00:00:00+00:00','2026-04-18','src','本文','news')"
    )
    cur.execute(
        "INSERT INTO topics VALUES(1,'T1','タイトル1','news','2026-04-18T00:00:00+00:00')"
    )
    cur.execute("INSERT INTO topic_articles VALUES(1,1,1)")
    cur.execute(
        "INSERT INTO topic_insights VALUES(1,5,'analysis','要約テスト','[]','[]','[]','{}','{}',"
        "'2026-04-18T00:00:00+00:00')"
    )
    conn.commit()

    sections = render_main.render_news_region_page(conn.cursor(), "jp", limit_each=10)
    html = _render_news_html(sections)

    # perspective_digest が空/NULLの記事では何も表示しない（HTML上は元々表示していなかった。
    # トップページ軽量化以降はJSON側でも perspective_digest キー自体を持たないことを確認する）
    assert "立場別くわしい解説" not in html
    news_section = next(s for s in sections if s["count"] > 0)
    item = (news_section["rows"] or news_section["other_rows"])[0]
    payload = render_main._insight_payload(item)
    assert "perspective_digest" not in payload


def test_render_forecast_skips_empty_placeholder_and_shows_fallback(tmp_path: Path):
    """既存の壊れたレポート（### 1. 予測1 空殻）でも、render 時に空殻を除去して
    フォールバック注記を出すことを確認する。"""
    conn = _bootstrap_forecast_db(tmp_path, _BROKEN_FORECAST_MD)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    render_main.render_forecast_page(docs_dir, "2026-04-20T00:00:00+00:00", conn.cursor())

    html = (docs_dir / "forecast" / "index.html").read_text(encoding="utf-8")
    # 空殻プレースホルダは描画されない
    assert ">予測1<" not in html
    # フォールバック注記が出る
    assert "この時間軸の予測は今回生成されませんでした" in html
    # 有効な予測は描画される
    assert "実予測" in html
    assert "これは本物の予測内容です" in html
