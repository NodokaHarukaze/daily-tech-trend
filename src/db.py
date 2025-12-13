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
  title_ja TEXT,
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
    conn = sqlite3.connect(DB_PATH)
    return conn

def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
