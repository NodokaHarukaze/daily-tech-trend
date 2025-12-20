from urllib.parse import urlsplit, urlunsplit
from db import connect

import time

def _now_sec():
    return time.perf_counter()

def normalize(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", ""))

def main():
    t0 = _now_sec()
    print("[TIME] step=normalize start")
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, url FROM articles")
    for i, url in cur.fetchall():
        cur.execute("UPDATE articles SET url_norm=? WHERE id=?", (normalize(url), i))
    conn.commit()
    conn.close()

    print(f"[TIME] step=normalize end sec={_now_sec() - t0:.1f}")

if __name__ == "__main__":
    main()
