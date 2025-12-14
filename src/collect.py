# src/collect.py
from __future__ import annotations

import feedparser
import yaml
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from db import init_db, connect


def load_feed_list(cfg: dict):
    # 旧形式: feeds: [{url, category, source}, ...]
    if isinstance(cfg.get("feeds"), list):
        return [
            {"url": x["url"], "category": x.get("category"), "source": x.get("source", "")}
            for x in cfg["feeds"]
        ]

    # 新形式: sources: [{url, category, name}, ...]
    if isinstance(cfg.get("sources"), list):
        return [
            {"url": x["url"], "category": x.get("category"), "source": x.get("name", x.get("source", ""))}
            for x in cfg["sources"]
        ]

    raise KeyError("sources.yaml must contain 'feeds' or 'sources' list.")


def normalize_published_at(entry) -> str:
    """
    RSSの published/updated (RFC2822等) を ISO8601(UTC) に正規化して返す。
    変換できない場合は空文字。
    """
    s = getattr(entry, "published", None) or getattr(entry, "updated", None) or ""
    if not s:
        return ""

    # feedparserは parsedate が入る場合もある（struct_time 等）ので、それも吸収
    try:
        dt = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            # struct_time -> datetime(UTC扱い)
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
        else:
            dt = parsedate_to_datetime(s)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""


def main():
    init_db()

    with open("src/sources.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    feed_list = load_feed_list(cfg)

    conn = connect()
    cur = conn.cursor()

    for feed in feed_list:
        d = feedparser.parse(feed["url"])
        for e in getattr(d, "entries", [])[:50]:
            link = getattr(e, "link", None)
            title = getattr(e, "title", None)
            if not link or not title:
                continue

            published_at = normalize_published_at(e)
            fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

            cur.execute(
                """
                INSERT OR IGNORE INTO articles
                (url, title, source, category, published_at, fetched_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    link,
                    title,
                    feed.get("source", ""),
                    feed.get("category", "") or "",
                    published_at,
                    fetched_at,
                ),
            )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
