"""Review table schema and initialization."""

from app.config import Settings
from app.parking_db import connect

REVIEW_TABLES = """
CREATE TABLE IF NOT EXISTS parking_reviews (
    id TEXT PRIMARY KEY,
    parking_id TEXT NOT NULL REFERENCES parking_lots(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    user_nickname TEXT NOT NULL,
    content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 300),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_parking_reviews_lot_created
    ON parking_reviews(parking_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_parking_reviews_user ON parking_reviews(user_id);
"""


def init_db(settings: Settings) -> None:
    with connect(settings) as connection:
        connection.executescript(REVIEW_TABLES)
