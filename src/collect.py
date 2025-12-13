import feedparser, yaml
from datetime import datetime, timezone
from db import init_db, connect

def main():
    init_db()
    now = datetime.now(timezone.utc).isoformat()

    with open("src/sources.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    conn = connect()
    cur = conn.cursor()

    for feed in cfg["feeds"]:
        d = feedparser.parse(feed["url"])
        for e in d.entries[:30]:
            if not getattr(e, "link", None):
                continue
            cur.execute("""
                INSERT OR IGNORE INTO articles
                (url, url_norm, title, source, category, published_at, fetched_at)
                VALUES (?,?,?,?,?,?,?)
            """, (
                e.link,
                e.link,
                e.title,
                feed["source"],
                feed["category"],
                getattr(e, "published", ""),
                now
            ))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()
