from db import connect

MAP = {
    "製造": "manufacturing",
    "AI": "ai",
    "開発": "dev",
    "システム": "system",
    "セキュリティ": "security",
}

def main():
    conn = connect()
    cur = conn.cursor()

    # articles
    for src, dst in MAP.items():
        cur.execute("UPDATE articles SET category=? WHERE category=?", (dst, src))

    # topics
    for src, dst in MAP.items():
        cur.execute("UPDATE topics SET category=? WHERE category=?", (dst, src))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()
