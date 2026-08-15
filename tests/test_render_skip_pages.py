"""RENDER_SKIP_PAGES によるページ生成スキップのテスト。

実際に使っているのが news と forecast のみになったため、
tech / diff / entity の生成を止められるようにした。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import render_main
from render_main import is_page_skipped, skipped_pages


def test_no_env_skips_nothing(monkeypatch):
    monkeypatch.delenv("RENDER_SKIP_PAGES", raising=False)
    assert skipped_pages() == set()
    assert is_page_skipped("tech") is False


def test_empty_env_skips_nothing(monkeypatch):
    monkeypatch.setenv("RENDER_SKIP_PAGES", "")
    assert skipped_pages() == set()
    assert is_page_skipped("tech") is False


def test_single_page(monkeypatch):
    monkeypatch.setenv("RENDER_SKIP_PAGES", "tech")
    assert skipped_pages() == {"tech"}
    assert is_page_skipped("tech") is True
    assert is_page_skipped("news") is False


def test_multiple_pages(monkeypatch):
    monkeypatch.setenv("RENDER_SKIP_PAGES", "tech,diff,entity")
    assert skipped_pages() == {"tech", "diff", "entity"}
    for name in ("tech", "diff", "entity"):
        assert is_page_skipped(name) is True
    assert is_page_skipped("news") is False
    assert is_page_skipped("forecast") is False


def test_whitespace_and_case_are_tolerated(monkeypatch):
    monkeypatch.setenv("RENDER_SKIP_PAGES", " Tech , DIFF ,, entity ")
    assert skipped_pages() == {"tech", "diff", "entity"}
    assert is_page_skipped(" TECH ") is True


def test_env_is_reread_each_call(monkeypatch):
    """モジュール読み込み時に固定されず、実行時の値が効くこと。"""
    monkeypatch.setenv("RENDER_SKIP_PAGES", "tech")
    assert is_page_skipped("tech") is True
    monkeypatch.setenv("RENDER_SKIP_PAGES", "diff")
    assert is_page_skipped("tech") is False
    assert is_page_skipped("diff") is True


def test_news_and_forecast_are_never_skipped_by_these_names(monkeypatch):
    """実際に使っているページ名が誤って一致しないことの確認。"""
    monkeypatch.setenv("RENDER_SKIP_PAGES", "tech,diff,entity")
    assert not is_page_skipped("news")
    assert not is_page_skipped("forecast")
    assert not is_page_skipped("forecast_hits")
    assert not is_page_skipped("ops")
    assert not is_page_skipped("search")


def test_helpers_are_exported():
    assert callable(render_main.skipped_pages)
    assert callable(render_main.is_page_skipped)
