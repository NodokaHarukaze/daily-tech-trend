import sqlite3
from pathlib import Path

DB_PATH = Path("data/state.sqlite")

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT UNIQUE NOT NULL,
  url_norm TEXT NOT NULL,
  title TEXT NOT NULL,
  source TEXT,
  category TEXT,
  published_at TEXT,
  fetched_at TEXT,
  content TEXT
);

CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_key TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  category TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS topic_articles (
  topic_id INTEGER NOT NULL,
  article_id INTEGER NOT NULL,
  PRIMARY KEY(topic_id, article_id)
);

CREATE TABLE IF NOT EXISTS edges (
  topic_id INTEGER NOT NULL,
  parent_article_id INTEGER NOT NULL,
  child_article_id INTEGER NOT NULL,
  PRIMARY KEY(topic_id, parent_article_id, child_article_id)
);
"""

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)

def _add_column_if_missing(conn, table: str, column: str, coldef: str):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")

def init_db():
    conn = connect()
    conn.executescript(SCHEMA)

    _add_column_if_missing(conn, "articles", "title_ja", "TEXT")
    _add_column_if_missing(conn, "topics", "title_ja", "TEXT")   # ← ★これを追加

    conn.commit()
    conn.close()
