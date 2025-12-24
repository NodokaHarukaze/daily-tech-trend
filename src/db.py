# src/db.py
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path("data/state.sqlite")


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = connect()
    cur = conn.cursor()

    # ---- articles ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS articles (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT,
      title TEXT,
      url TEXT,
      url_norm TEXT,
      content TEXT,
      category TEXT,
      published_at TEXT,
      fetched_at TEXT
    )
    """)

    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_url
    ON articles(url)
    """)

    # ---- topics ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS topics (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT,
      title_ja TEXT,
      category TEXT,
      score_48h INTEGER DEFAULT 0,
      created_at TEXT
    )
    """)

    # ---- topic_articles ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS topic_articles (
      topic_id INTEGER,
      article_id INTEGER,
      PRIMARY KEY (topic_id, article_id)
    )
    """)

    # ---- topic_insights (LLM結果) ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS topic_insights (
      topic_id INTEGER PRIMARY KEY,
      importance INTEGER,
      type TEXT,
      summary TEXT,
      key_points TEXT,
      impact_guess TEXT,
      next_actions TEXT,
      evidence_urls TEXT,
      tags TEXT,
      perspectives TEXT,          
      updated_at TEXT
    )
    """)

    ensure_column(cur, "topic_insights", "tags", "TEXT")
    ensure_column(cur, "topic_insights", "perspectives", "TEXT")
    ensure_column(cur, "topic_insights", "evidence_urls", "TEXT")
    ensure_column(cur, "topic_insights", "src_article_id", "INTEGER")
    ensure_column(cur, "topic_insights", "src_hash", "TEXT")


    conn.commit()
    conn.close()

def ensure_column(cur, table: str, col: str, coltype: str):
    cur.execute(f"PRAGMA table_info({table})")
    cols = {r[1] for r in cur.fetchall()}
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def recompute_score_48h():
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
    UPDATE topics
    SET score_48h = (
      SELECT COUNT(*)
      FROM topic_articles ta
      JOIN articles a ON a.id = ta.article_id
      WHERE ta.topic_id = topics.id
        AND datetime(a.published_at) >= datetime('now', '-48 hours')
    )
    """)

    conn.commit()
    conn.close()


def now():
    return datetime.utcnow().isoformat(timespec="seconds")
