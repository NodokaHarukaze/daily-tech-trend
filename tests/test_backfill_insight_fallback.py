"""フォールバック insight のバックフィルスクリプトのテスト。"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import backfill_insight_fallback as bf
from llm_insights_pipeline import NEWS_FALLBACK_KEY_POINT, compute_src_hash, pick_topic_inputs

_FB = '["%s"]' % NEWS_FALLBACK_KEY_POINT
_OK = '["事実1","事実2","事実3"]'


def _db():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("create table topics (id integer primary key, title text, title_ja text,"
                " category text, score_48h integer, kind text)")
    cur.execute("create table articles (id integer primary key, kind text, source text, title text,"
                " title_ja text, url text, content text, category text, region text default '',"
                " published_at text, fetched_at text)")
    cur.execute("create table topic_articles (topic_id integer, article_id integer)")
    cur.execute("create table topic_insights (topic_id integer primary key, importance integer,"
                " summary text, key_points text, src_hash text, updated_at text)")
    return conn


def _add(cur, tid, aid, *, title_ja, body, url, key_points, updated_at,
         topic_category="news", article_category="news", kind="news"):
    cur.execute("insert into topics values (?,'en',?,?,5,?)", (tid, title_ja, topic_category, kind))
    cur.execute(
        "insert into articles values (?,?, 'SrcX','en title',?,?,?,?,'jp',?,?)",
        (aid, kind, title_ja, url, body, article_category,
         '2026-08-01T00:00:00+00:00', '2026-08-01T01:00:00+00:00'),
    )
    cur.execute("insert into topic_articles values (?,?)", (tid, aid))
    cur.execute("insert into topic_insights values (?,70,?,?,'h',?)",
                (tid, title_ja, key_points, updated_at))


def test_picks_only_fallback_rows():
    conn = _db()
    cur = conn.cursor()
    _add(cur, 1, 10, title_ja="壊れた", body="本文", url="https://e/1",
         key_points=_FB, updated_at="2026-08-20T00:00:00+00:00")
    _add(cur, 2, 20, title_ja="正常", body="本文", url="https://e/2",
         key_points=_OK, updated_at="2026-08-20T00:00:00+00:00")
    conn.commit()

    rows = bf.pick_fallback_rows(conn, "all", 100)
    assert [r["topic_id"] for r in rows] == [1]


def test_since_filters_older_rows():
    conn = _db()
    cur = conn.cursor()
    _add(cur, 1, 10, title_ja="新しい", body="本文", url="https://e/1",
         key_points=_FB, updated_at="2026-08-20T00:00:00+00:00")
    _add(cur, 2, 20, title_ja="古い", body="本文", url="https://e/2",
         key_points=_FB, updated_at="2026-05-01T00:00:00+00:00")
    conn.commit()

    assert [r["topic_id"] for r in bf.pick_fallback_rows(conn, "2026-07-01", 100)] == [1]
    assert len(bf.pick_fallback_rows(conn, "all", 100)) == 2


def test_newest_first_order():
    conn = _db()
    cur = conn.cursor()
    for tid, ts in ((1, "2026-08-10T00:00:00+00:00"),
                    (2, "2026-08-20T00:00:00+00:00"),
                    (3, "2026-08-15T00:00:00+00:00")):
        _add(cur, tid, tid * 10, title_ja=f"t{tid}", body="本文", url=f"https://e/{tid}",
             key_points=_FB, updated_at=ts)
    conn.commit()

    assert [r["topic_id"] for r in bf.pick_fallback_rows(conn, "all", 100)] == [2, 3, 1]


def test_title_and_category_match_pick_topic_inputs():
    """title / category の決め方が pick_topic_inputs とずれると src_hash がずれ、
    次回の --rescue が「内容が変わった」と誤認して再生成を繰り返す。"""
    conn = _db()
    cur = conn.cursor()
    # news トピック（記事側の title_ja を採用する）
    _add(cur, 1, 10, title_ja="ニュース見出し", body="本文", url="https://e/1",
         key_points=_FB, updated_at="2026-08-20T00:00:00+00:00")
    # tech トピック（トピック側のタイトルを優先し、category は記事側を採る）
    _add(cur, 2, 20, title_ja="技術記事", body="本文2", url="https://e/2",
         key_points=_FB, updated_at="2026-08-20T00:00:00+00:00",
         topic_category="dev", article_category="dev", kind="tech")
    conn.commit()

    picked = {r["topic_id"]: r for r in pick_topic_inputs(conn, rescue=True)}
    mine = {r["topic_id"]: r for r in bf.pick_fallback_rows(conn, "all", 100)}
    assert set(mine) == {1, 2}

    # pick_topic_inputs が拾う行（news）は title / category / src_hash が一致すること
    assert 1 in picked
    for tid in set(picked) & set(mine):
        ref, r = picked[tid], mine[tid]
        assert r["title"] == ref["topic_title"]
        assert r["category"] == ref["category"]
        assert compute_src_hash(r["title"], r["url"], r["body"]) == \
               compute_src_hash(ref["topic_title"], ref["url"], ref["body"])

    # tech トピックは rescue の対象外なので pick_topic_inputs には出ないが、
    # このスクリプトは拾う。その際もトピック側タイトル・記事側カテゴリを使う
    assert 2 not in picked
    assert mine[2]["title"] == "技術記事"
    assert mine[2]["category"] == "dev"


class _FakeDatetime:
    """now() だけを差し替える時計。最後の値は使い回す。"""

    def __init__(self, times):
        self._times = list(times)

    def now(self):
        return self._times.pop(0) if len(self._times) > 1 else self._times[0]


class TestPipelineWindowGuard:
    def test_disabled_guard_returns_immediately(self, monkeypatch):
        slept = []
        monkeypatch.setattr(bf.time, "sleep", lambda s: slept.append(s))
        bf._wait_out_pipeline_window(False)
        assert slept == []

    def test_waits_inside_pipeline_window(self, monkeypatch):
        """定時実行の時間帯なら待つ（DB・LLM の取り合いを避ける）"""
        real = bf.datetime
        times = [real(2026, 8, 24, 6, 10, 0), real(2026, 8, 24, 6, 40, 0)]
        monkeypatch.setattr(bf, "datetime", _FakeDatetime(times))
        slept = []
        monkeypatch.setattr(bf.time, "sleep", lambda s: slept.append(s))
        bf._wait_out_pipeline_window(True)
        assert slept and slept[0] == (bf.PIPELINE_WINDOW_MIN - 10) * 60

    def test_no_wait_outside_pipeline_window(self, monkeypatch):
        real = bf.datetime
        monkeypatch.setattr(bf, "datetime", _FakeDatetime([real(2026, 8, 24, 17, 50, 0)]))
        slept = []
        monkeypatch.setattr(bf.time, "sleep", lambda s: slept.append(s))
        bf._wait_out_pipeline_window(True)
        assert slept == []
