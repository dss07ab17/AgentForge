"""Offline change recipe for the bundled brownfield sample (examples/brownfield/linkshort_v1).

In LLM mode the Coder agent produces edits of exactly this shape
(``{"file", "old", "new"}`` anchored search/replace, or ``{"file", "new",
"create": true}``) from the impact analysis. Offline, this recipe stands in for
the model so the whole brownfield workflow (impact analysis -> targeted edits ->
regression tests -> diff approval -> patch export) is reproducible.

Each edit group is keyed by the engineering task that owns it.
"""
from __future__ import annotations

FINGERPRINT = {"app/store.py": "INSERT OR REPLACE INTO links", "app/service.py": "def shorten(conn, url"}


def matches(repo_files: dict[str, str]) -> bool:
    return all(marker in repo_files.get(path, "") for path, marker in FINGERPRINT.items())


STORAGE_EDITS = [
    {"file": "app/db.py",
     "old": "    created REAL NOT NULL\n);",
     "new": "    created REAL NOT NULL,\n    expires_at REAL\n);"},
    {"file": "app/db.py",
     "old": "    conn.executescript(SCHEMA)\n    return conn\n",
     "new": (
         "    conn.executescript(SCHEMA)\n    _migrate(conn)\n    return conn\n\n\n"
         "def _migrate(conn):\n"
         '    """Forward-only, idempotent migration for databases created by v1."""\n'
         "    columns = {row[1] for row in conn.execute(\"PRAGMA table_info(links)\")}\n"
         "    if \"expires_at\" not in columns:\n"
         "        conn.execute(\"ALTER TABLE links ADD COLUMN expires_at REAL\")\n"
         "        conn.commit()\n")},
    {"file": "app/store.py",
     "old": '"""Persistence helpers for links."""\nimport time\n',
     "new": ('"""Persistence helpers for links."""\nimport sqlite3\nimport time\n\n\n'
             "class DuplicateCode(Exception):\n"
             '    """The short code is already taken (primary-key violation)."""\n')},
    {"file": "app/store.py",
     "old": (
         "def save_link(conn, code, url):\n    conn.execute(\n"
         '        "INSERT OR REPLACE INTO links (code, url, created) VALUES (?, ?, ?)",\n'
         "        (code, url, time.time()),\n    )\n    conn.commit()\n"),
     "new": (
         "def save_link(conn, code, url, expires_at=None):\n"
         '    """Insert a new link. Never overwrites: raises DuplicateCode if the code exists."""\n'
         "    try:\n        conn.execute(\n"
         '            "INSERT INTO links (code, url, created, expires_at) VALUES (?, ?, ?, ?)",\n'
         "            (code, url, time.time(), expires_at),\n        )\n"
         "    except sqlite3.IntegrityError as exc:\n        conn.rollback()\n"
         "        raise DuplicateCode(code) from exc\n    conn.commit()\n\n\n"
         "def get_link(conn, code):\n"
         '    """Return (url, expires_at) for a code, or None."""\n'
         '    row = conn.execute("SELECT url, expires_at FROM links WHERE code = ?", (code,)).fetchone()\n'
         "    return (row[0], row[1]) if row else None\n")},
    {"file": "app/store.py",
     "old": ('def get_url(conn, code):\n'
             '    row = conn.execute("SELECT url FROM links WHERE code = ?", (code,)).fetchone()\n'
             "    return row[0] if row else None\n"),
     "new": ("def get_url(conn, code):\n"
             '    """Backward-compatible helper kept for existing callers."""\n'
             "    link = get_link(conn, code)\n    return link[0] if link else None\n")},
]

SERVICE_EDITS = [
    {"file": "app/service.py",
     "old": '"""Business logic for shortening and resolving links."""\nfrom app import store\n',
     "new": ('"""Business logic for shortening and resolving links."""\nimport time\n\nfrom app import store\n')},
    {"file": "app/service.py",
     "old": "class LinkNotFound(Exception):\n    pass\n",
     "new": ("MAX_CODE_ATTEMPTS = 10\nMIN_TTL_SECONDS = 1\nMAX_TTL_SECONDS = 10 * 365 * 24 * 3600\n\n\n"
             "class LinkNotFound(Exception):\n    pass\n\n\n"
             "class LinkExpired(Exception):\n    pass\n\n\n"
             "class CodeSpaceExhausted(Exception):\n"
             '    """Could not find a free code after MAX_CODE_ATTEMPTS tries."""\n')},
    {"file": "app/service.py",
     "old": ("def shorten(conn, url, code_factory=new_code):\n"
             '    if not url.startswith(("http://", "https://")):\n'
             '        raise ValueError("url must start with http:// or https://")\n'
             "    code = code_factory()\n    store.save_link(conn, code, url)\n    return code\n"),
     "new": ("def shorten(conn, url, code_factory=new_code, ttl_seconds=None, clock=None):\n"
             '    if not isinstance(url, str) or not url.startswith(("http://", "https://")):\n'
             '        raise ValueError("url must start with http:// or https://")\n'
             "    expires_at = None\n"
             "    if ttl_seconds is not None:\n"
             "        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):\n"
             '            raise ValueError("ttl_seconds must be an integer")\n'
             "        if not MIN_TTL_SECONDS <= ttl_seconds <= MAX_TTL_SECONDS:\n"
             '            raise ValueError(f"ttl_seconds must be between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS}")\n'
             "        expires_at = (clock or time.time)() + ttl_seconds\n"
             "    for _ in range(MAX_CODE_ATTEMPTS):\n"
             "        code = code_factory()\n"
             "        try:\n"
             "            store.save_link(conn, code, url, expires_at)\n"
             "            return code\n"
             "        except store.DuplicateCode:\n"
             "            continue  # collision: never overwrite, try a fresh code\n"
             '    raise CodeSpaceExhausted("could not allocate a unique code")\n')},
    {"file": "app/service.py",
     "old": ("def resolve(conn, code):\n    url = store.get_url(conn, code)\n"
             "    if url is None:\n        raise LinkNotFound(code)\n    return url\n"),
     "new": ("def resolve(conn, code, clock=None):\n    link = store.get_link(conn, code)\n"
             "    if link is None:\n        raise LinkNotFound(code)\n"
             "    url, expires_at = link\n"
             "    if expires_at is not None and (clock or time.time)() >= expires_at:\n"
             "        raise LinkExpired(code)\n    return url\n")},
]

WEB_EDITS = [
    {"file": "app/web.py",
     "old": ('            code = service.shorten(conn, data.get("url", ""))\n'
             "        except ValueError as exc:\n"
             '            return _json(start_response, "400 Bad Request", {"error": str(exc)})\n'),
     "new": ('            code = service.shorten(conn, data.get("url", ""), ttl_seconds=data.get("ttl_seconds"))\n'
             "        except ValueError as exc:\n"
             '            return _json(start_response, "400 Bad Request", {"error": str(exc)})\n'
             "        except service.CodeSpaceExhausted:\n"
             '            return _json(start_response, "503 Service Unavailable", {"error": "try again"})\n')},
    {"file": "app/web.py",
     "old": ("        except service.LinkNotFound:\n"
             '            return _json(start_response, "404 Not Found", {"error": "not found"})\n'),
     "new": ("        except service.LinkNotFound:\n"
             '            return _json(start_response, "404 Not Found", {"error": "not found"})\n'
             "        except service.LinkExpired:\n"
             '            return _json(start_response, "410 Gone", {"error": "link expired"})\n')},
]

TEST_EDITS = [
    {"file": "tests/test_expiry_and_collisions.py", "create": True, "new": '''"""Regression + feature tests for link expiry and the code-collision overwrite bug."""
import io
import json
import sqlite3

import pytest

from app import service, store, web
from app.db import connect


@pytest.fixture
def conn():
    return connect(":memory:")


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def scripted(*codes):
    pending = list(codes)
    return lambda: pending.pop(0)


def test_collision_does_not_overwrite_existing_link(conn):
    """Regression for the v1 bug: INSERT OR REPLACE silently replaced links."""
    first = service.shorten(conn, "https://first.example", code_factory=scripted("aaaaa"))
    second = service.shorten(conn, "https://second.example", code_factory=scripted("aaaaa", "bbbbb"))
    assert (first, second) == ("aaaaa", "bbbbb")
    assert service.resolve(conn, "aaaaa") == "https://first.example"


def test_store_rejects_duplicate_codes(conn):
    store.save_link(conn, "abcde", "https://a.example")
    with pytest.raises(store.DuplicateCode):
        store.save_link(conn, "abcde", "https://b.example")
    assert store.get_url(conn, "abcde") == "https://a.example"


def test_gives_up_when_code_space_is_exhausted(conn):
    service.shorten(conn, "https://a.example", code_factory=scripted("zzzzz"))
    with pytest.raises(service.CodeSpaceExhausted):
        service.shorten(conn, "https://b.example", code_factory=scripted(*["zzzzz"] * service.MAX_CODE_ATTEMPTS))


def test_link_expires_after_ttl(conn):
    clock = Clock()
    code = service.shorten(conn, "https://a.example", ttl_seconds=60, clock=clock)
    clock.t += 59
    assert service.resolve(conn, code, clock=clock) == "https://a.example"
    clock.t += 1
    with pytest.raises(service.LinkExpired):
        service.resolve(conn, code, clock=clock)


def test_links_without_ttl_never_expire(conn):
    code = service.shorten(conn, "https://a.example")
    assert service.resolve(conn, code, clock=lambda: 10**12) == "https://a.example"


@pytest.mark.parametrize("ttl", [0, -1, "60", 1.5, True, 10**10])
def test_invalid_ttl_rejected(conn, ttl):
    with pytest.raises(ValueError):
        service.shorten(conn, "https://a.example", ttl_seconds=ttl)


def test_migration_adds_column_to_v1_database(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE links (code TEXT PRIMARY KEY, url TEXT NOT NULL, created REAL NOT NULL)")
    old.execute("INSERT INTO links VALUES ('old01', 'https://legacy.example', 0)")
    old.commit()
    old.close()
    conn = connect(path)
    assert service.resolve(conn, "old01") == "https://legacy.example"
    connect(path)  # idempotent


def call(method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(raw)),
               "wsgi.input": io.BytesIO(raw)}
    status = {}
    payload = b"".join(web.app(environ, lambda s, h: status.update(code=int(s.split()[0]), headers=dict(h))))
    return status["code"], status["headers"], payload


def test_http_expired_link_returns_410(monkeypatch):
    monkeypatch.setattr(web, "CONN", connect(":memory:"))
    status, _, payload = call("POST", "/shorten", {"url": "https://a.example", "ttl_seconds": 1})
    assert status == 201
    code = json.loads(payload)["code"]
    assert call("GET", f"/{code}")[0] == 302
    monkeypatch.setattr(service.time, "time", lambda: 10**12)
    assert call("GET", f"/{code}")[0] == 410


def test_http_invalid_ttl_returns_400(monkeypatch):
    monkeypatch.setattr(web, "CONN", connect(":memory:"))
    assert call("POST", "/shorten", {"url": "https://a.example", "ttl_seconds": "soon"})[0] == 400
'''},
]

DOC_EDITS = [
    {"file": "README.md",
     "old": "- `GET /<code>` -> `302` redirect\n",
     "new": ("- `POST /shorten` also accepts optional `\"ttl_seconds\"` (integer, 1 to 10 years); "
             "the link expires after that many seconds\n"
             "- `GET /<code>` -> `302` redirect, `404` if unknown, `410 Gone` if expired\n\n"
             "## Changelog\n\n### v1.1\n"
             "- Added optional link expiry (`ttl_seconds`, new nullable `expires_at` column, "
             "automatic idempotent migration for v1 databases).\n"
             "- Fixed: a newly generated code that collided with an existing one silently overwrote "
             "that link (`INSERT OR REPLACE`). Inserts are now strict and collisions are retried.\n")},
]

GROUPS = {"storage": STORAGE_EDITS, "service": SERVICE_EDITS, "web": WEB_EDITS,
          "tests": TEST_EDITS, "docs": DOC_EDITS}
