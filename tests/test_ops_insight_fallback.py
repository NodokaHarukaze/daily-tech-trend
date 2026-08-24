"""運用ページの「要約失敗率」指標のテスト。

2026-08-24: LLM 応答が壊れて定型文に差し替わった insight が約85%あったのに、
ログには `[OK] insight saved` が並ぶだけで8週間以上検知できなかった。
件数ではなく率を出して再発を早期に見つけるための指標。
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import render_main
from llm_insights_pipeline import NEWS_FALLBACK_KEY_POINT
from render_main import INSIGHT_FALLBACK_WARN_PCT, compute_insight_fallback_stats

CUTOFF = "2026-08-24 00:00:00"


def _db(rows):
    """rows: (updated_at, key_points) のリスト"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "create table topic_insights (topic_id integer primary key, updated_at text, key_points text)"
    )
    for i, (updated_at, kp) in enumerate(rows, 1):
        cur.execute("insert into topic_insights values (?,?,?)", (i, updated_at, kp))
    conn.commit()
    return cur


_OK = '["事実1","事実2","事実3"]'
_FB = '["%s"]' % NEWS_FALLBACK_KEY_POINT


def test_healthy_returns_zero_rate():
    cur = _db([("2026-08-24T06:00:00+00:00", _OK)] * 5)
    s = compute_insight_fallback_stats(cur, CUTOFF)
    assert s["insight_fallback_rate"] == 0.0
    assert s["insight_fallback_24h"] == 0
    assert s["insight_fallback_24h_total"] == 5
    assert s["insight_fallback_warn"] is False


def test_broken_rate_triggers_warning():
    # 修正前の実態（約85%）を再現
    cur = _db([("2026-08-24T06:00:00+00:00", _FB)] * 17
              + [("2026-08-24T06:00:00+00:00", _OK)] * 3)
    s = compute_insight_fallback_stats(cur, CUTOFF)
    assert s["insight_fallback_rate"] == 85.0
    assert s["insight_fallback_warn"] is True
    assert s["insight_fallback_rate"] >= INSIGHT_FALLBACK_WARN_PCT


def test_old_rows_excluded_from_rate_but_counted_in_total():
    cur = _db([
        ("2026-08-24T06:00:00+00:00", _OK),      # 24h内・正常
        ("2026-08-20T06:00:00+00:00", _FB),      # 期間外・壊れている
        ("2026-08-19T06:00:00+00:00", _FB),      # 期間外・壊れている
    ])
    s = compute_insight_fallback_stats(cur, CUTOFF)
    assert s["insight_fallback_rate"] == 0.0, "率は直近24時間だけで見る"
    assert s["insight_fallback_24h_total"] == 1
    assert s["insight_fallback_total"] == 2, "未修復の総件数は全期間で数える"


def test_no_recent_rows_is_not_a_warning():
    """生成が0件の時間帯に 0/0 で警告を出さない（ゼロ除算も起こさない）"""
    cur = _db([("2026-08-20T06:00:00+00:00", _FB)])
    s = compute_insight_fallback_stats(cur, CUTOFF)
    assert s["insight_fallback_24h_total"] == 0
    assert s["insight_fallback_rate"] == 0.0
    assert s["insight_fallback_warn"] is False


def test_query_failure_does_not_break_ops_page(monkeypatch):
    """指標1つのために運用ページ全体を落とさない"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()  # topic_insights テーブルが存在しない
    logged = []
    monkeypatch.setattr(render_main, "_log_render_error",
                        lambda *a, **k: logged.append(a))
    s = compute_insight_fallback_stats(cur, CUTOFF)
    assert s["insight_fallback_warn"] is False
    assert s["insight_fallback_rate"] == 0.0
    assert logged, "失敗はログに残す"
