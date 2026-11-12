"""Database access: a single SQLite connection factory and schema."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    code TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    created REAL NOT NULL
);
"""


def connect(path="linkshort.db"):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.executescript(SCHEMA)
    return conn
