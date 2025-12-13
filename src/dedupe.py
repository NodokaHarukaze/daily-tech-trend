from rapidfuzz import fuzz
from db import connect

def main():
    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT id, title FROM articles ORDER BY id DESC")
    rows = cur.fetchall()

    seen = {}
    for i, title in rows:
        for si, st in seen.items():
            if fuzz.ratio(title, st) > 92:
                cur.execute("DELETE FROM articles WHERE id=?", (i,))
                break
        else:
            seen[i] = title

    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()
