"""SQLite schema and connections. External API data is refreshed by the sync command."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import Settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS parking_lots (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    latitude REAL,
    longitude REAL,
    fee_type TEXT NOT NULL DEFAULT 'unknown',
    fee_description TEXT NOT NULL DEFAULT '',
    hourly_price INTEGER,
    accessible INTEGER,
    total_spaces INTEGER,
    weekday_open TEXT,
    weekday_close TEXT,
    saturday_open TEXT,
    saturday_close TEXT,
    holiday_open TEXT,
    holiday_close TEXT,
    operating_days TEXT,
    reference_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_parking_location ON parking_lots(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_parking_name ON parking_lots(name);
CREATE TABLE IF NOT EXISTS realtime_facilities (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    latitude REAL,
    longitude REAL
);
CREATE TABLE IF NOT EXISTS realtime_links (
    parking_id TEXT PRIMARY KEY REFERENCES parking_lots(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL UNIQUE REFERENCES realtime_facilities(source_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS realtime_status (
    source_id TEXT PRIMARY KEY,
    total_spaces INTEGER,
    available_spaces INTEGER,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS favorites (
    token_hash TEXT NOT NULL REFERENCES sessions(token_hash) ON DELETE CASCADE,
    parking_id TEXT NOT NULL REFERENCES parking_lots(id) ON DELETE CASCADE,
    PRIMARY KEY (token_hash, parking_id)
);
CREATE TABLE IF NOT EXISTS ratings (
    token_hash TEXT NOT NULL REFERENCES sessions(token_hash) ON DELETE CASCADE,
    parking_id TEXT NOT NULL REFERENCES parking_lots(id) ON DELETE CASCADE,
    score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    PRIMARY KEY (token_hash, parking_id)
);
CREATE TABLE IF NOT EXISTS sync_progress (
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    page_size INTEGER NOT NULL,
    next_page INTEGER NOT NULL,
    total INTEGER NOT NULL,
    PRIMARY KEY (kind, scope, page_size)
);
"""


@contextmanager
def connect(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(settings.database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(settings: Settings) -> None:
    with connect(settings) as connection:
        connection.executescript(SCHEMA)

