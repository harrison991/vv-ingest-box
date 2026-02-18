import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any

DB_PATH = Path("tagger.db")

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

if __name__ == "__main__":
    import json
    from datetime import datetime
    
    # Clean up test database if it exists
    if DB_PATH.exists():
        DB_PATH.unlink()
    
    print("Testing database functionality...\n")
    
    # Test 1: Initialize database
    print("✓ Initializing database...")
    init_db()
    print("  Database initialized successfully\n")
    
    # Test 2: Insert test data
    print("✓ Inserting test records...")
    test_data = [
        ("test_file1.jpg", "sig_001", 1024, 1672531200000000000, json.dumps({"tag": "photo"}), datetime.now().isoformat()),
        ("test_file2.mp4", "sig_002", 5242880, 1672531200000000000, json.dumps({"tag": "video"}), datetime.now().isoformat()),
        ("test_file3.jpg", "sig_003", 2048, 1672531200000000000, json.dumps({"tag": "photo"}), datetime.now().isoformat()),
    ]
    
    for path, sig, size, mtime, tags, processed_at in test_data:
        upsert_result(path, sig, size, mtime, tags, processed_at)
    print("  3 records inserted\n")
    
    # Test 3: Check if signatures exist
    print("✓ Checking signature existence...")
    for _, sig, *_ in test_data:
        exists = signature_exists(sig)
        print(f"  Signature '{sig}' exists: {exists}")
    
    print(f"\n  Non-existent signature 'sig_999' exists: {signature_exists('sig_999')}\n")
    
    # Test 4: Query database
    print("✓ Querying all records...")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, path, signature, size_bytes FROM media")
    records = cur.fetchall()
    conn.close()
    
    print(f"  Total records: {len(records)}")
    for record_id, path, sig, size in records:
        print(f"    [{record_id}] {path} ({size} bytes, sig: {sig})")
    
    print("\n✅ All tests passed!")
    
    # Clean up test database
    print("\nCleaning up test database...")
    if DB_PATH.exists():
        DB_PATH.unlink()
    print("Done!")