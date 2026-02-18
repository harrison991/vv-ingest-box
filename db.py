import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any

DB_PATH = Path("/var/lib/vv_ingest/tagger.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL,
  signature TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  tags_json TEXT NOT NULL,
  processed_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_media_sig ON media(signature);
CREATE INDEX IF NOT EXISTS idx_media_path ON media(path);
"""

def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    conn = get_conn()
    with conn:
        conn.executescript(SCHEMA)
    conn.close()

def signature_exists(signature: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM media WHERE signature = ? LIMIT 1", (signature,))
    row = cur.fetchone()
    conn.close()
    return row is not None

def upsert_result(path: str, signature: str, size_bytes: int, mtime_ns: int, tags_json: str, processed_at: str):
    conn = get_conn()
    with conn:
        # Ignore if signature exists (dedupe)
        conn.execute(
            "INSERT OR IGNORE INTO media (path, signature, size_bytes, mtime_ns, tags_json, processed_at) VALUES (?,?,?,?,?,?)",
            (path, signature, size_bytes, mtime_ns, tags_json, processed_at)
        )
    conn.close()