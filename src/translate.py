import io
import os
import re
import sys

import requests
from requests import RequestException
from db import connect

import time

# Windows のバッチ実行では標準出力が cp932 になり、記事タイトルやエラーメッセージに
# 含まれる非ASCII文字（例: ö）を print した瞬間に UnicodeEncodeError で
# プロセスが落ちる。2026-08-23 の停止はこれが原因だったため UTF-8 に差し替える。
# ストリームを差し替えず reconfigure するのは、pytest の出力キャプチャを壊さないため。
def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, io.UnsupportedOperation):
            pass  # 差し替え済みストリーム（pytest 等）では何もしない


if os.name == "nt":
    _force_utf8_stdio()


def _now_sec():
    return time.perf_counter()

API = "https://translate.googleapis.com/translate_a/single"

# 429（レート制限）がこの回数だけ連続したら、その回の翻訳は打ち切って次回へ回す。
# 翻訳はベストエフォートであり、粘って叩き続けても制限が解けないため。
RATE_LIMIT_ABORT = 5

# 日本語文字（ひらがな・カタカナ・漢字・全角記号）
JA_CHARS = re.compile(r"[　-〿぀-ゟ゠-ヿ一-鿿＀-￯]")


class RateLimited(Exception):
    """Google 翻訳の 429 を上位へ伝えるための内部例外。"""


def translate(text: str, retries: int = 2) -> str:
    params = {"client":"gtx","sl":"en","tl":"ja","dt":"t","q":text}
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(API, params=params, timeout=15)
            if r.status_code == 429:
                raise RateLimited("429 Too Many Requests")
            r.raise_for_status()
            data = r.json()
            return "".join([x[0] for x in data[0] if x and x[0]])
        except RateLimited:
            raise
        except (RequestException, ValueError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise last_err

def looks_english(text: str) -> bool:
    """翻訳する価値があるか（英字を含み、日本語文字を含まない）。

    以前は「英字を1文字でも含む」だけで判定していたため、
    『Windows運用管理で"やってはいけない"3つのこと』のような
    既に日本語のタイトルまで翻訳APIへ投げてレート制限を招いていた。
    """
    text = text or ""
    if not re.search(r"[A-Za-z]", text):
        return False
    return not JA_CHARS.search(text)

def ensure_column(cur, table: str, col: str, coltype: str = "TEXT"):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]  # r[1] = column name
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

def translate_news_titles(conn, limit: int = 400):
    """
    articles.kind='news' かつ title_ja が空のものを翻訳して埋める。

    英字を含まない純日本語タイトルは SQL 段階で除外し、
    英字を含むが日本語のタイトルは title_ja=title で確定させる。
    こうしないと翻訳不要なタイトルが毎回 LIMIT 枠を占有し続ける。
    """
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT id, title
        FROM articles
        WHERE kind IN ('news','tech')
          AND title IS NOT NULL AND title != ''
          AND (title_ja IS NULL OR title_ja = '')
          AND title GLOB '*[A-Za-z]*'
        ORDER BY published_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    targets = [(i, t) for i, t in rows if looks_english(t)]
    skipped = [(i, t) for i, t in rows if not looks_english(t)]

    print(f"[translate] news titles (to translate): {len(targets)} / skipped(ja): {len(skipped)}")

    # 翻訳不要（既に日本語）は原文をそのまま確定させて対象から外す。
    # 参照側は COALESCE(title_ja, title) なので表示は変わらない。
    for article_id, title in skipped:
        cur.execute("UPDATE articles SET title_ja=? WHERE id=?", (title, article_id))

    n_ok = 0
    consecutive_429 = 0
    for article_id, title in targets:
        try:
            ja = translate(title)
            ja = (ja or "").strip()
            consecutive_429 = 0
        except RateLimited:
            consecutive_429 += 1
            if consecutive_429 >= RATE_LIMIT_ABORT:
                print(
                    f"[WARN] translate rate limited {consecutive_429} times in a row; "
                    f"aborting this run (remaining titles will be retried next run)"
                )
                break
            time.sleep(2.0 * consecutive_429)
            continue
        except (RequestException, ValueError) as e:
            print(f"[WARN] translate failed id={article_id} title={title[:80]!r} err={e}")
            continue

        if not ja:
            print(f"[WARN] translate returned empty id={article_id} title={title[:80]!r}")
            continue

        cur.execute(
            "UPDATE articles SET title_ja=? WHERE id=?",
            (ja, article_id),
        )
        n_ok += 1

    conn.commit()
    print(f"[translate] news titles updated: {n_ok}")

def translate_topic_titles(conn):
    """topics を日本語化（トップ表示に直結）。"""
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM topics WHERE title_ja IS NULL OR title_ja = ''")
    rows = cur.fetchall()

    consecutive_429 = 0
    for tid, title in rows:
        if not title or not looks_english(title):
            continue
        try:
            ja = translate(title)
            consecutive_429 = 0
        except RateLimited:
            consecutive_429 += 1
            if consecutive_429 >= RATE_LIMIT_ABORT:
                print(
                    f"[WARN] translate topic rate limited {consecutive_429} times in a row; "
                    f"aborting this run"
                )
                break
            time.sleep(2.0 * consecutive_429)
            continue
        except (RequestException, ValueError) as e:
            print(f"[WARN] translate topic failed topic_id={tid} title={title[:80]!r} err={e}")
            continue

        if not ja:
            print(f"[WARN] translate topic empty topic_id={tid} title={title[:80]!r}")
            continue
        cur.execute("UPDATE topics SET title_ja=? WHERE id=?", (ja, tid))

    conn.commit()

def main():
    t0 = _now_sec()
    print("[TIME] step=translate start")
    conn = connect()
    cur = conn.cursor()

    ensure_column(cur, "articles", "title_ja", "TEXT")

    # 翻訳は公開の必須要件ではない（参照側は COALESCE(title_ja, title)）。
    # ここで異常終了すると run_daily.bat が render / git push まで到達せず
    # サイト更新が止まるため、想定外の例外もログに残して正常終了する。
    try:
        translate_news_titles(conn, limit=600)
        translate_topic_titles(conn)
    except Exception as e:
        print(f"[WARN] translate step aborted err={type(e).__name__}: {e}")

    conn.close()

    print(f"[TIME] step=translate end sec={_now_sec() - t0:.1f}")

if __name__ == "__main__":
    main()
