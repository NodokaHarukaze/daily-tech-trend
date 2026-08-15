"""LLM の生成対象から特定の kind を外せることのテスト。

技術動向ダイジェスト（tech ページ）を参照しなくなった場合に、
LLM_MAX_SEC の予算を news 側へ寄せるための仕組み。
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm_insights_pipeline import pick_topic_inputs


def _setup_db(*, topics_has_kind=True):
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    kind_col = "kind text," if topics_has_kind else ""
    cur.execute(
        f"create table topics (id integer primary key, title text, title_ja text,"
        f" {kind_col} category text, score_48h integer)"
    )
    cur.execute(
        "create table articles (id integer primary key, kind text, source text, title text,"
        " title_ja text, url text, content text, category text, region text default '',"
        " published_at text, fetched_at text)"
    )
    cur.execute("create table topic_articles (topic_id integer, article_id integer)")
    cur.execute(
        "create table topic_insights (topic_id integer primary key, importance integer,"
        " summary text, src_hash text)"
    )
    return conn


def _add(cur, tid, aid, kind, *, topics_has_kind=True):
    if topics_has_kind:
        cur.execute(
            "insert into topics (id, title, title_ja, kind, category, score_48h)"
            " values (?, 'en', 'タイトル', ?, ?, 5)",
            (tid, kind, "news" if kind == "news" else "ai"),
        )
    else:
        cur.execute(
            "insert into topics (id, title, title_ja, category, score_48h)"
            " values (?, 'en', 'タイトル', ?, 5)",
            (tid, "news" if kind == "news" else "ai"),
        )
    cur.execute(
        "insert into articles values (?, ?, 'SrcX', 'en title', 'タイトル', ?, '本文', ?, '',"
        " '2026-01-01T00:00:00+00:00', '2026-01-01T01:00:00+00:00')",
        (aid, kind, f"https://e/{aid}", "news" if kind == "news" else "ai"),
    )
    cur.execute("insert into topic_articles values (?, ?)", (tid, aid))


def test_skip_kinds_excludes_tech_topics():
    conn = _setup_db()
    cur = conn.cursor()
    _add(cur, 1, 10, "tech")
    _add(cur, 2, 20, "news")
    conn.commit()

    rows = pick_topic_inputs(conn, skip_kinds=("tech",))
    assert [r["topic_id"] for r in rows] == [2]


def test_no_skip_kinds_keeps_everything():
    conn = _setup_db()
    cur = conn.cursor()
    _add(cur, 1, 10, "tech")
    _add(cur, 2, 20, "news")
    conn.commit()

    rows = pick_topic_inputs(conn)
    assert sorted(r["topic_id"] for r in rows) == [1, 2]


def test_skip_kinds_is_case_insensitive_and_ignores_blanks():
    conn = _setup_db()
    cur = conn.cursor()
    _add(cur, 1, 10, "tech")
    _add(cur, 2, 20, "news")
    conn.commit()

    rows = pick_topic_inputs(conn, skip_kinds=(" TECH ", "", None))
    assert [r["topic_id"] for r in rows] == [2]


def test_skip_kinds_can_exclude_multiple():
    conn = _setup_db()
    cur = conn.cursor()
    _add(cur, 1, 10, "tech")
    _add(cur, 2, 20, "news")
    conn.commit()

    assert pick_topic_inputs(conn, skip_kinds=("tech", "news")) == []


def test_skip_kinds_falls_back_to_article_kind_when_topics_lack_column():
    # 旧DB/テストDB互換: topics.kind が無くても記事側の kind で判定できる
    conn = _setup_db(topics_has_kind=False)
    cur = conn.cursor()
    _add(cur, 1, 10, "tech", topics_has_kind=False)
    _add(cur, 2, 20, "news", topics_has_kind=False)
    conn.commit()

    rows = pick_topic_inputs(conn, skip_kinds=("tech",))
    assert [r["topic_id"] for r in rows] == [2]


def test_skip_kinds_still_applies_under_rescue():
    conn = _setup_db()
    cur = conn.cursor()
    _add(cur, 1, 10, "tech")
    _add(cur, 2, 20, "news")
    # 両方とも insight 済みにして rescue でのみ拾われる状態にする
    cur.execute("insert into topic_insights values (1, 50, 'ok', 'h1')")
    cur.execute("insert into topic_insights values (2, 50, 'ok', 'h2')")
    conn.commit()

    rows = pick_topic_inputs(conn, rescue=True, skip_kinds=("tech",))
    assert [r["topic_id"] for r in rows] == [2]
