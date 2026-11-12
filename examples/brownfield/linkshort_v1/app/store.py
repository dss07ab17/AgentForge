"""Persistence helpers for links."""
import time


def save_link(conn, code, url):
    conn.execute(
        "INSERT OR REPLACE INTO links (code, url, created) VALUES (?, ?, ?)",
        (code, url, time.time()),
    )
    conn.commit()


def get_url(conn, code):
    row = conn.execute("SELECT url FROM links WHERE code = ?", (code,)).fetchone()
    return row[0] if row else None
