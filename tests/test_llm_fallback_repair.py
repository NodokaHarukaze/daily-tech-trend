"""定型フォールバック文言に差し替わった insight を再生成対象にすることのテスト。

2026-08-24 調査: LLM 応答の JSON が途中で切れると key_points が
`NEWS_FALLBACK_KEY_POINT` に差し替わるが、summary（記事タイトル）と
importance は埋まるため `_needs_repair` が壊れていると判定できず、
src_hash 一致で永久にスキップされ続けていた（news insight の約85%）。
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import llm_insights_local
from llm_insights_local import _needs_repair
from llm_insights_pipeline import (
    NEWS_FALLBACK_KEY_POINT,
    compute_src_hash,
    pick_topic_inputs,
)


def _setup_db(*, with_key_points=True):
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "create table topics (id integer primary key, title text, title_ja text,"
        " category text, score_48h integer)"
    )
    cur.execute(
        "create table articles (id integer primary key, kind text, source text, title text,"
        " title_ja text, url text, content text, category text, region text default '',"
        " published_at text, fetched_at text)"
    )
    cur.execute("create table topic_articles (topic_id integer, article_id integer)")
    if with_key_points:
        cur.execute(
            "create table topic_insights (topic_id integer primary key, importance integer,"
            " summary text, key_points text, src_hash text)"
        )
    else:
        # 旧スキーマ / テスト DB 互換（key_points 列が無い）
        cur.execute(
            "create table topic_insights (topic_id integer primary key, importance integer,"
            " summary text, src_hash text)"
        )
    return conn


def _add_news_topic(cur, tid, aid, *, title_ja, body, url):
    cur.execute("insert into topics values (?, 'en', ?, 'news', 5)", (tid, title_ja))
    cur.execute(
        "insert into articles values (?, 'news', 'SrcX', 'en title', ?, ?, ?, 'news', '',"
        " '2026-01-01T00:00:00+00:00', '2026-01-01T01:00:00+00:00')",
        (aid, title_ja, url, body),
    )
    cur.execute("insert into topic_articles values (?, ?)", (tid, aid))


def _hash_for(title_ja, url, body):
    return compute_src_hash(title_ja, url, body)


class TestNeedsRepairFallback:
    def test_fallback_row_needs_repair(self):
        assert _needs_repair(
            {"prev_importance": 70, "prev_summary_empty": 0, "prev_is_fallback": 1}
        ) is True

    def test_healthy_row_still_skipped(self):
        assert _needs_repair(
            {"prev_importance": 70, "prev_summary_empty": 0, "prev_is_fallback": 0}
        ) is False


def test_pick_topic_inputs_flags_fallback_row():
    conn = _setup_db()
    cur = conn.cursor()
    _add_news_topic(cur, 1, 10, title_ja="壊れた要約", body="本文", url="https://e/1")
    cur.execute(
        "insert into topic_insights values (1, 70, '壊れた要約', ?, 'h')",
        ('["%s"]' % NEWS_FALLBACK_KEY_POINT,),
    )
    conn.commit()

    rows = pick_topic_inputs(conn, rescue=True)
    assert len(rows) == 1
    assert rows[0]["prev_is_fallback"] == 1


def test_pick_topic_inputs_does_not_flag_healthy_row():
    conn = _setup_db()
    cur = conn.cursor()
    _add_news_topic(cur, 1, 10, title_ja="まともな要約", body="本文", url="https://e/1")
    cur.execute(
        "insert into topic_insights values (1, 70, 'ちゃんとした要約', ?, 'h')",
        ('["事実1","事実2","事実3"]',),
    )
    conn.commit()

    rows = pick_topic_inputs(conn, rescue=True)
    assert rows[0]["prev_is_fallback"] == 0


def test_pick_topic_inputs_without_key_points_column():
    """key_points 列が無いテスト DB でもクエリが壊れない（互換）"""
    conn = _setup_db(with_key_points=False)
    cur = conn.cursor()
    _add_news_topic(cur, 1, 10, title_ja="旧スキーマ", body="本文", url="https://e/1")
    cur.execute("insert into topic_insights values (1, 70, '要約', 'h')")
    conn.commit()

    rows = pick_topic_inputs(conn, rescue=True)
    assert rows[0]["prev_is_fallback"] == 0


def _run_main(monkeypatch, conn):
    called = []
    monkeypatch.setattr(llm_insights_local, "connect", lambda: conn)
    monkeypatch.setattr(
        llm_insights_local, "call_llm",
        lambda title, category, url, body, kind="": called.append(title) or "{}",
    )
    monkeypatch.setattr(
        llm_insights_local, "postprocess_insight",
        lambda raw, r: {"importance": 50, "summary": "s"},
    )
    monkeypatch.setattr(llm_insights_local, "upsert_insight", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["llm_insights_local.py", "--rescue", "--delay", "0"])
    llm_insights_local.main()
    return called


def test_rescue_regenerates_fallback_insight_with_unchanged_hash(monkeypatch):
    conn = _setup_db()
    cur = conn.cursor()
    _add_news_topic(cur, 1, 10, title_ja="定型文になった記事", body="本文", url="https://e/1")
    cur.execute(
        "insert into topic_insights values (1, 70, '定型文になった記事', ?, ?)",
        ('["%s"]' % NEWS_FALLBACK_KEY_POINT,
         _hash_for("定型文になった記事", "https://e/1", "本文")),
    )
    conn.commit()

    called = _run_main(monkeypatch, conn)
    assert called == ["定型文になった記事"], "フォールバック文言の insight は作り直す"


def test_rescue_still_skips_healthy_insight(monkeypatch):
    conn = _setup_db()
    cur = conn.cursor()
    _add_news_topic(cur, 1, 10, title_ja="正常な記事", body="本文", url="https://e/1")
    cur.execute(
        "insert into topic_insights values (1, 70, 'ちゃんとした要約', ?, ?)",
        ('["事実1","事実2","事実3"]',
         _hash_for("正常な記事", "https://e/1", "本文")),
    )
    conn.commit()

    called = _run_main(monkeypatch, conn)
    assert called == [], "正常な insight は従来どおりスキップする"
