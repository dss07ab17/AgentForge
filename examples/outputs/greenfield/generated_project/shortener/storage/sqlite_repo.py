"""SQLite implementation of the link repository.

* One connection per thread (sqlite3 connections are not thread-safe).
* WAL journal mode so readers (redirects) never block on the analytics writer.
* ``:memory:`` is mapped to a shared-cache in-memory database so every thread
  sees the same data (used by tests).
"""
from __future__ import annotations

import itertools
import sqlite3
import threading
from collections import Counter
from pathlib import Path

from ..errors import DuplicateCode
from ..models import ClickEvent, Link, from_iso, to_iso

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_memory_ids = itertools.count(1)


class SQLiteRepository:
    def __init__(self, path: str = "shortener.db") -> None:
        if path == ":memory:":
            self._dsn, self._uri = f"file:shortener-mem-{next(_memory_ids)}?mode=memory&cache=shared", True
        else:
            self._dsn, self._uri = path, False
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._keeper = self._connect()  # keeps a shared in-memory DB alive
        self._keeper.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    # ---------------------------------------------------------- connections
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._dsn, uri=self._uri, timeout=10, check_same_thread=False,
                               isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        if not self._uri:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        with self._lock:
            self._all.append(conn)
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = self._connect()
        return conn

    def close(self) -> None:
        with self._lock:
            for conn in self._all:
                conn.close()
            self._all.clear()

    # ---------------------------------------------------------------- links
    def insert_link(self, link: Link) -> None:
        try:
            self._conn.execute(
                "INSERT INTO links (code, target_url, created_at, expires_at, is_custom) VALUES (?, ?, ?, ?, ?)",
                (link.code, link.target_url, to_iso(link.created_at), to_iso(link.expires_at), int(link.is_custom)),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateCode(link.code) from exc

    def get_link(self, code: str) -> Link | None:
        row = self._conn.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        if row is None:
            return None
        return Link(
            code=row["code"],
            target_url=row["target_url"],
            created_at=from_iso(row["created_at"]),
            is_custom=bool(row["is_custom"]),
            expires_at=from_iso(row["expires_at"]),
        )

    def delete_link(self, code: str) -> bool:
        cur = self._conn.execute("DELETE FROM links WHERE code = ?", (code,))
        return cur.rowcount > 0

    def ping(self) -> bool:
        return self._conn.execute("SELECT 1").fetchone()[0] == 1

    # ------------------------------------------------------------ analytics
    def record_clicks(self, events: list[ClickEvent]) -> None:
        if not events:
            return
        conn = self._conn
        per_code = Counter(e.code for e in events)
        last = {}
        for e in events:
            last[e.code] = max(last.get(e.code, e.occurred_at), e.occurred_at)
        conn.execute("BEGIN")
        try:
            conn.executemany(
                "UPDATE links SET click_count = click_count + ?, last_click_at = ? WHERE code = ?",
                [(n, to_iso(last[code]), code) for code, n in per_code.items()],
            )
            conn.executemany(
                "INSERT INTO click_events (code, occurred_at, day, referrer_host, user_agent) "
                "SELECT ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM links WHERE code = ?)",
                [(e.code, to_iso(e.occurred_at), e.occurred_at.date().isoformat(), e.referrer_host,
                  (e.user_agent or "")[:256] or None, e.code) for e in events],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def stats(self, code: str) -> dict:
        row = self._conn.execute("SELECT click_count, last_click_at FROM links WHERE code = ?", (code,)).fetchone()
        if row is None:
            return {}
        result = {"code": code, "total_clicks": row["click_count"], "last_click_at": row["last_click_at"]}
        by_day = self._conn.execute(
            "SELECT day, COUNT(*) AS n FROM click_events WHERE code = ? GROUP BY day ORDER BY day", (code,)
        ).fetchall()
        refs = self._conn.execute(
            "SELECT COALESCE(referrer_host, 'direct') AS ref, COUNT(*) AS n FROM click_events "
            "WHERE code = ? GROUP BY ref ORDER BY n DESC, ref LIMIT 10", (code,)
        ).fetchall()
        result["clicks_by_day"] = [{"day": r["day"], "clicks": r["n"]} for r in by_day]
        result["top_referrers"] = [{"referrer": r["ref"], "clicks": r["n"]} for r in refs]
        return result
