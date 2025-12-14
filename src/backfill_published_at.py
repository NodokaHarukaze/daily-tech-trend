from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

DB_PATH = "data/state.sqlite"


def norm(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""

    # よくあるZ表記を吸収
    s2 = s.replace("Z", "+00:00")

    # まず ISO8601 を試す（YYYY-MM-DD... で始まるものを広く救う）
    if len(s2) >= 10 and s2[4] == "-" and s2[7] == "-":
        try:
            # 'YYYY-MM-DD HH:MM:SS' → 'T' に寄せる
            if " " in s2 and "T" not in s2:
                s2 = s2.replace(" ", "T", 1)
            dt = datetime.fromisoformat(s2)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
        except Exception:
            pass

    # 次に RFC2822 等を試す
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    rows = cur.execute(
        "select id, published_at from articles where published_at is not null and published_at != ''"
    ).fetchall()

    upd = 0
    bad = 0
    for aid, pub in rows:
        n = norm(pub)
        if not n:
            bad += 1
            continue
        if n != pub:
            cur.execute("update articles set published_at=? where id=?", (n, aid))
            upd += 1

    conn.commit()
    conn.close()
    print("updated:", upd, "/", len(rows), "unparsed:", bad)


if __name__ == "__main__":
    main()
