"""generate_perspective_digest の kind 除外のテスト。

tech の insight 生成を止めても、この処理は kind の区別なく topic_insights 全体を
対象にしていたため、tech の既存 insight に立場別解説を付け続けていた
（未生成の内訳は tech 10,446件 / news 5,778件で対象の64%が tech）。
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from generate_perspective_digest import _fetch_targets


def _setup(*, topics_has_kind=True):
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    kind_col = "kind text," if topics_has_kind else ""
    cur.execute(
        f"create table topics (id integer primary key, title text, title_ja text, {kind_col} category text)"
    )
    cur.execute(
        "create table topic_insights (topic_id integer primary key, summary text,"
        " perspectives text, evidence_urls text, perspective_digest text, updated_at text)"
    )
    return conn, cur


def _add(cur, tid, kind, digest, *, topics_has_kind=True, updated_at="2026-01-01"):
    if topics_has_kind:
        cur.execute(
            "insert into topics (id, title, title_ja, kind, category) values (?, 'en', 'ja', ?, 'ai')",
            (tid, kind),
        )
    else:
        cur.execute(
            "insert into topics (id, title, title_ja, category) values (?, 'en', 'ja', 'ai')", (tid,)
        )
    cur.execute(
        "insert into topic_insights values (?, '要約', '{}', '[]', ?, ?)",
        (tid, digest, updated_at),
    )


def test_skip_kinds_excludes_tech():
    conn, cur = _setup()
    _add(cur, 1, "tech", None)
    _add(cur, 2, "news", None)
    conn.commit()

    rows = _fetch_targets(cur, 10, ("tech",))
    assert [r[0] for r in rows] == [2]


def test_no_skip_kinds_keeps_everything():
    conn, cur = _setup()
    _add(cur, 1, "tech", None)
    _add(cur, 2, "news", None)
    conn.commit()

    rows = _fetch_targets(cur, 10)
    assert sorted(r[0] for r in rows) == [1, 2]


def test_skip_kinds_applies_to_both_null_and_empty_digest():
    """WHERE が "A OR B" のため、AND を括弧なしで足すと
    "A OR (B AND kind条件)" となり NULL 側に除外が効かなくなる。その回帰テスト。"""
    conn, cur = _setup()
    _add(cur, 1, "tech", None)      # A: perspective_digest IS NULL
    _add(cur, 2, "tech", "{}")      # B: perspective_digest = '{}'
    _add(cur, 3, "news", None)
    conn.commit()

    rows = _fetch_targets(cur, 10, ("tech",))
    assert [r[0] for r in rows] == [3], "NULL 側の tech も除外されなければならない"


def test_skip_kinds_is_case_insensitive():
    conn, cur = _setup()
    _add(cur, 1, "TECH", None)
    _add(cur, 2, "news", None)
    conn.commit()

    rows = _fetch_targets(cur, 10, (" Tech ",))
    assert [r[0] for r in rows] == [2]


def test_rows_without_digest_are_still_returned_newest_first():
    conn, cur = _setup()
    _add(cur, 1, "news", None, updated_at="2026-01-01")
    _add(cur, 2, "news", None, updated_at="2026-03-01")
    conn.commit()

    rows = _fetch_targets(cur, 10, ("tech",))
    assert [r[0] for r in rows] == [2, 1]


def test_skip_kinds_ignored_when_topics_lack_kind_column():
    # 旧DB互換: kind 列が無ければ除外条件を組み立てずに全件返す（エラーにしない）
    conn, cur = _setup(topics_has_kind=False)
    _add(cur, 1, "tech", None, topics_has_kind=False)
    _add(cur, 2, "news", None, topics_has_kind=False)
    conn.commit()

    rows = _fetch_targets(cur, 10, ("tech",))
    assert sorted(r[0] for r in rows) == [1, 2]
