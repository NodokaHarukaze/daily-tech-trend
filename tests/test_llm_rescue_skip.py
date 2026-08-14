"""rescue モードで「内容が変わっていない insight」を再生成しないことのテスト。

従来 --rescue は src_hash が同じでも必ず作り直していたため、
本番実測では1回あたりの候補120件中61件（50%）が、1文字も変わっていない入力から
同じ結果を作り直すだけの空回りになっていた。
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import llm_insights_local
from llm_insights_local import _needs_repair
from llm_insights_pipeline import compute_src_hash, pick_topic_inputs


def _setup_db():
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
    # pick_topic_inputs は news カテゴリでは記事側の title_ja を topic_title に採用する
    return compute_src_hash(title_ja, url, body)


class TestNeedsRepair:
    def test_importance_zero_needs_repair(self):
        assert _needs_repair({"prev_importance": 0, "prev_summary_empty": 0}) is True

    def test_empty_summary_needs_repair(self):
        assert _needs_repair({"prev_importance": 50, "prev_summary_empty": 1}) is True

    def test_healthy_row_does_not_need_repair(self):
        assert _needs_repair({"prev_importance": 50, "prev_summary_empty": 0}) is False

    def test_missing_columns_are_treated_as_needing_repair(self):
        # 旧DB互換: 列が無い場合は importance=0 相当とみなして作り直す
        assert _needs_repair({}) is True


def test_pick_topic_inputs_exposes_repair_columns():
    conn = _setup_db()
    cur = conn.cursor()
    _add_news_topic(cur, 1, 10, title_ja="ニュース", body="本文", url="https://e/1")
    cur.execute("insert into topic_insights values (1, 70, '要約あり', 'h')")
    conn.commit()

    rows = pick_topic_inputs(conn, rescue=True)
    assert len(rows) == 1
    assert rows[0]["prev_importance"] == 70
    assert rows[0]["prev_summary_empty"] == 0


def _run_main(monkeypatch, conn):
    """call_llm / postprocess_insight / upsert_insight をモックして main() を走らせ、
    実際に LLM へ投げられた topic_id のリストを返す。"""
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
    monkeypatch.setattr(
        llm_insights_local, "upsert_insight",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(sys, "argv", ["llm_insights_local.py", "--rescue", "--delay", "0"])
    llm_insights_local.main()
    return called


def test_rescue_skips_topic_whose_content_is_unchanged(monkeypatch):
    conn = _setup_db()
    cur = conn.cursor()
    _add_news_topic(cur, 1, 10, title_ja="変わらないニュース", body="本文", url="https://e/1")
    # 現在の内容と一致するハッシュを保存済み＝再生成しても同じ結果にしかならない
    cur.execute(
        "insert into topic_insights values (1, 70, '要約あり', ?)",
        (_hash_for("変わらないニュース", "https://e/1", "本文"),),
    )
    conn.commit()

    called = _run_main(monkeypatch, conn)
    assert called == [], "内容未変更のトピックに LLM を呼んではいけない"


def test_rescue_still_processes_topic_whose_content_changed(monkeypatch):
    conn = _setup_db()
    cur = conn.cursor()
    _add_news_topic(cur, 1, 10, title_ja="続報あり", body="新しい本文", url="https://e/1")
    cur.execute("insert into topic_insights values (1, 70, '要約あり', 'old-hash')")
    conn.commit()

    called = _run_main(monkeypatch, conn)
    assert called == ["続報あり"], "続報で内容が変わったトピックは再生成する"


def test_rescue_still_repairs_broken_insight_with_unchanged_hash(monkeypatch):
    conn = _setup_db()
    cur = conn.cursor()
    _add_news_topic(cur, 1, 10, title_ja="壊れたニュース", body="本文", url="https://e/1")
    # ハッシュは一致しているが importance=0 のまま＝壊れているので作り直す
    cur.execute(
        "insert into topic_insights values (1, 0, '', ?)",
        (_hash_for("壊れたニュース", "https://e/1", "本文"),),
    )
    conn.commit()

    called = _run_main(monkeypatch, conn)
    assert called == ["壊れたニュース"], "壊れた insight はハッシュが同じでも作り直す"


def test_topic_without_insight_is_always_processed(monkeypatch):
    conn = _setup_db()
    cur = conn.cursor()
    _add_news_topic(cur, 1, 10, title_ja="未生成ニュース", body="本文", url="https://e/1")
    conn.commit()

    called = _run_main(monkeypatch, conn)
    assert called == ["未生成ニュース"]
