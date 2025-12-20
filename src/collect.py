# src/collect.py
from __future__ import annotations

import feedparser
import yaml
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from db import init_db, connect

from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import re
import time

def _now_sec():
    return time.perf_counter()

def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)
    
DROP_QS = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","ref","fbclid","gclid"}
HEADERS = {
    "User-Agent": "DailyTechTrend/1.0 (+https://github.com/yourname/daily-tech-trend)"
}

def normalize_url(u: str) -> str:
    sp = urlsplit(u)
    qs = [(k,v) for k,v in parse_qsl(sp.query, keep_blank_values=True) if k not in DROP_QS]
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(qs), ""))  # fragment


def load_feed_list(cfg: dict):
    # 旧形式: feeds: [{url, category, source}, ...]
    if isinstance(cfg.get("feeds"), list):
        return [
            {
                "url": x["url"],
                "category": x.get("category"),
                "source": x.get("source", ""),
                "kind": x.get("kind", "tech"),
                "region": x.get("region", ""),
                "limit": x.get("limit", 30),
            }
            for x in cfg["feeds"]
        ]

    # 新形式: sources: [{url, category, name, kind, region}, ...]
    if isinstance(cfg.get("sources"), list):
        return [
            {
                "url": x["url"],
                "category": x.get("category"),
                "source": x.get("name", x.get("source", "")),
                "kind": x.get("kind", "tech"),
                "region": x.get("region", ""),
                "limit": x.get("limit", 30),
            }
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
    t0 = _now_sec()
    print("[TIME] step=collect start")
    init_db()

    with open("src/sources.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    feed_list = load_feed_list(cfg)

    conn = connect()
    cur = conn.cursor()

    for feed in feed_list:
        try:
            #d = feedparser.parse(feed["url"])
            d = feedparser.parse(feed["url"], request_headers=HEADERS)
            if getattr(d, "bozo", 0):
                # 壊れたXMLでも entries が取れることがあるので続行はする
                print(f"[WARN] malformed feed url={feed['url']}")
                pass
                
            limit = feed.get("limit", 30)
            for e in getattr(d, "entries", [])[:limit]:
                raw_link = getattr(e, "link", None)
                if not raw_link:
                    continue
                link = normalize_url(raw_link)
                title = getattr(e, "title", None)
                if not link or not title:
                    continue
                content = ""

                if getattr(e, "content", None):
                    if isinstance(e.content, list) and e.content:
                        content = e.content[0].get("value", "")
                elif getattr(e, "summary", None):
                    content = e.summary

                content = strip_html(content)

                published_at = normalize_published_at(e)
                fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

                cur.execute(
                    """
                    INSERT OR IGNORE INTO articles
                    (url, title, content, source, category, published_at, fetched_at, kind, region)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        link,
                        title,
                        content,
                        feed.get("source", ""),
                        feed.get("category", "") or "",
                        published_at,
                        fetched_at,
                        feed.get("kind", "tech"),
                        feed.get("region", "") or "",
                    ),
                )


        except Exception as e:
            print(f"[WARN] feed parse failed url={feed['url']} err={e}")
            continue
    conn.commit()
    conn.close()

    sec = _now_sec() - t0
    print(f"[TIME] step=collect end sec={sec:.1f}")

if __name__ == "__main__":
    main()
