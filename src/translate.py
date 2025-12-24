import re
import requests
from db import connect

import time

def _now_sec():
    return time.perf_counter()

API = "https://translate.googleapis.com/translate_a/single"

def translate(text: str) -> str:
    params = {"client":"gtx","sl":"en","tl":"ja","dt":"t","q":text}
    r = requests.get(API, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return "".join([x[0] for x in data[0] if x and x[0]])

def looks_english(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text or ""))

def ensure_column(cur, table: str, col: str, coltype: str = "TEXT"):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]  # r[1] = column name
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

def main():
    t0 = _now_sec()
    print("[TIME] step=translate start")
    conn = connect()
    cur = conn.cursor()

    ensure_column(cur, "articles", "title_ja", "TEXT")

    # topics を日本語化（トップ表示に直結）
    # cur.execute("SELECT id, title FROM topics WHERE title_ja IS NULL LIMIT 100")
    cur.execute("SELECT id, title FROM topics WHERE title_ja IS NULL")
    rows = cur.fetchall()

    for tid, title in rows:
        if not title or not looks_english(title):
            continue
        try:
            ja = translate(title)
            if ja:
                cur.execute("UPDATE topics SET title_ja=? WHERE id=?", (ja, tid))
        except Exception:
            continue

    # --- news title translation ---
    cur.execute(
        """
        SELECT id, title
        FROM articles
        WHERE kind='news'
        AND title IS NOT NULL AND title != ''
        AND (title_ja IS NULL OR title_ja = '')
        ORDER BY id DESC
        LIMIT 200
        """
    )

    rows = cur.fetchall()
    print(f"[translate] news titles: {len(rows)}")

    for aid, title in rows:
        # 英語っぽいものだけ翻訳（既存の関数を使う想定）
        if not looks_english(title):
            continue

        try:
            ja = translate(title)
        except Exception as e:
            print(f"[translate][news] failed id={aid}: {e}")
            continue

        if ja and ja.strip():
            cur.execute(
                "UPDATE articles SET title_ja=? WHERE id=?",
                (ja.strip(), aid)
            )

    conn.commit()
    conn.close()

    print(f"[TIME] step=translate end sec={_now_sec() - t0:.1f}")

if __name__ == "__main__":
    main()
