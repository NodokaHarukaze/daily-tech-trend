"""translate ステップの回帰テスト。

2026-08-23 のパイプライン停止（translate が UnicodeEncodeError と 429 で
異常終了し、render / git push まで到達しなかった）の再発防止。
"""

import os
import sqlite3
import sys

import pytest
from requests import RequestException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import translate as T  # noqa: E402


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            title TEXT,
            title_ja TEXT,
            kind TEXT,
            published_at TEXT
        );
        CREATE TABLE topics (
            id INTEGER PRIMARY KEY,
            title TEXT,
            title_ja TEXT
        );
        """
    )
    return conn


def add_article(conn, aid, title, kind="news"):
    conn.execute(
        "INSERT INTO articles (id, title, title_ja, kind, published_at) VALUES (?,?,NULL,?,?)",
        (aid, title, kind, "2026-08-23T00:00:00"),
    )


class TestLooksEnglish:
    def test_英字のみの文は翻訳対象(self):
        assert T.looks_english("Every Chokepoint That Isn't Hormuz") is True

    def test_純日本語は翻訳対象外(self):
        assert T.looks_english("東武鉄道子会社を業務上過失致死容疑で捜索") is False

    def test_英字混じりの日本語は翻訳対象外(self):
        # 429 多発の原因。英字が1文字でもあれば翻訳していた旧判定への回帰防止
        assert T.looks_english("Windows運用管理で“やってはいけない”3つのこと") is False
        assert T.looks_english("TikTok、児童プライバシー訴訟で4億ドル支払いへ") is False

    def test_空とNoneは翻訳対象外(self):
        assert T.looks_english("") is False
        assert T.looks_english(None) is False


class TestTranslateNewsTitles:
    def test_英語タイトルだけ翻訳し日本語は原文で確定する(self, monkeypatch):
        conn = make_conn()
        add_article(conn, 1, "Steel Prices Rise in Europe")
        add_article(conn, 2, "東武鉄道子会社を捜索")          # 英字なし → SQL段階で除外
        add_article(conn, 3, "Windows運用管理の3つのこと")     # 英字あり日本語 → 原文で確定

        called = []

        def fake_translate(text, retries=2):
            called.append(text)
            return "翻訳結果"

        monkeypatch.setattr(T, "translate", fake_translate)
        T.translate_news_titles(conn, limit=10)

        assert called == ["Steel Prices Rise in Europe"]
        rows = dict(conn.execute("SELECT id, title_ja FROM articles").fetchall())
        assert rows[1] == "翻訳結果"
        assert rows[2] is None                       # 対象外なので触らない
        assert rows[3] == "Windows運用管理の3つのこと"  # 原文で確定し枠を占有しない

    def test_429が連続したら打ち切って例外を投げない(self, monkeypatch):
        conn = make_conn()
        for i in range(1, 21):
            add_article(conn, i, f"Steel News Number {i}")

        calls = []

        def always_429(text, retries=2):
            calls.append(text)
            raise T.RateLimited("429 Too Many Requests")

        monkeypatch.setattr(T, "translate", always_429)
        monkeypatch.setattr(T.time, "sleep", lambda s: None)

        T.translate_news_titles(conn, limit=50)  # 例外を投げないこと

        # RATE_LIMIT_ABORT 回で打ち切り、全20件を叩き続けない
        assert len(calls) == T.RATE_LIMIT_ABORT
        assert conn.execute(
            "SELECT COUNT(*) FROM articles WHERE title_ja IS NOT NULL"
        ).fetchone()[0] == 0

    def test_個別の通信エラーはスキップして継続する(self, monkeypatch):
        conn = make_conn()
        add_article(conn, 1, "First English Title")
        add_article(conn, 2, "Second English Title")

        def flaky(text, retries=2):
            if "First" in text:
                # 非ASCII文字を含むエラー（cp932 で落ちた実際のケース）
                raise RequestException("429 for url ...q=Iw+KöLn+Pleads")
            return "翻訳結果"

        monkeypatch.setattr(T, "translate", flaky)
        T.translate_news_titles(conn, limit=10)

        rows = dict(conn.execute("SELECT id, title_ja FROM articles").fetchall())
        assert rows[1] is None
        assert rows[2] == "翻訳結果"


class TestMainDoesNotBreakPipeline:
    def test_想定外の例外でもmainは異常終了しない(self, monkeypatch):
        """translate が落ちると run_daily.bat が render/push に到達しない。"""
        conn = make_conn()
        monkeypatch.setattr(T, "connect", lambda: conn)

        def boom(*a, **kw):
            raise UnicodeEncodeError("cp932", "ö", 0, 1, "illegal multibyte sequence")

        monkeypatch.setattr(T, "translate_news_titles", boom)

        T.main()  # 例外が外へ漏れないこと（rc=0 を維持）


class TestTranslateRateLimit:
    def test_429はRateLimitedとして即座に伝播しリトライしない(self, monkeypatch):
        attempts = []

        class Resp:
            status_code = 429

            def raise_for_status(self):
                raise AssertionError("429 では raise_for_status に到達しない")

        def fake_get(url, params=None, timeout=None):
            attempts.append(1)
            return Resp()

        monkeypatch.setattr(T.requests, "get", fake_get)
        with pytest.raises(T.RateLimited):
            T.translate("Steel News")
        assert len(attempts) == 1  # レート制限中の無駄なリトライをしない
