-- URL shortener schema (SQLite dialect; portable to PostgreSQL with minor type changes).
-- Generated from the architect's data model. Applied idempotently at startup.

CREATE TABLE IF NOT EXISTS links (
    code          TEXT PRIMARY KEY,            -- base62 short code or custom alias
    target_url    TEXT NOT NULL,
    created_at    TEXT NOT NULL,               -- ISO-8601 UTC
    expires_at    TEXT,                        -- ISO-8601 UTC, NULL = never expires
    is_custom     INTEGER NOT NULL DEFAULT 0,
    click_count   INTEGER NOT NULL DEFAULT 0,  -- denormalized counter for O(1) totals
    last_click_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_links_expires_at ON links(expires_at) WHERE expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS click_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL REFERENCES links(code) ON DELETE CASCADE,
    occurred_at   TEXT NOT NULL,
    day           TEXT NOT NULL,               -- YYYY-MM-DD, for daily roll-ups
    referrer_host TEXT,
    user_agent    TEXT
);

CREATE INDEX IF NOT EXISTS idx_click_events_code_day ON click_events(code, day);
