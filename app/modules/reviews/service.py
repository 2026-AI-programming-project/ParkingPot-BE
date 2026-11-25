"""Review persistence and business rules."""

import logging
import secrets
import sqlite3
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException

from app.config import Settings
from app.db.database import Database
from app.parking_db import connect

logger = logging.getLogger(__name__)
WITHDRAWN_AUTHOR_ID = str(UUID(int=0))
WITHDRAWN_AUTHOR_NAME = "탈퇴한 회원"


def list_reviews(settings: Settings, accounts: Database, parking_id: str) -> dict:
    with connect(settings) as connection:
        if (
            connection.execute("SELECT 1 FROM parking_lots WHERE id = ?", (parking_id,)).fetchone()
            is None
        ):
            raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
        connection.execute("ATTACH DATABASE ? AS membership", (str(accounts.path),))
        rows = connection.execute(
            """SELECT r.id, COALESCE(u.id, ?) AS user_id,
                      CASE WHEN u.id IS NULL THEN ? ELSE r.user_nickname END AS user_nickname,
                      r.content, r.created_at
               FROM parking_reviews r LEFT JOIN membership.users u ON u.id = r.user_id
               WHERE r.parking_id = ? ORDER BY r.created_at DESC, r.id DESC""",
            (WITHDRAWN_AUTHOR_ID, WITHDRAWN_AUTHOR_NAME, parking_id),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows)}


def create_review(
    settings: Settings, accounts: Database, parking_id: str, user: dict, content: str
) -> dict:
    content = content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="리뷰 내용을 입력해 주세요")
    created_at = datetime.now(UTC).isoformat()
    review_id = secrets.token_hex(16)
    # Serialize author validation and review creation with account deletion.
    with accounts.connect(write=True) as members:
        author = members.execute(
            "SELECT nickname FROM users WHERE id = ?", (user["id"],)
        ).fetchone()
        if author is None:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다")
        nickname = author["nickname"]
        with connect(settings) as connection:
            result = connection.execute(
                """INSERT INTO parking_reviews
                   (id, parking_id, user_id, user_nickname, content, created_at)
                   SELECT ?, id, ?, ?, ?, ? FROM parking_lots WHERE id = ?""",
                (review_id, user["id"], nickname, content, created_at, parking_id),
            )
            if not result.rowcount:
                raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
    return {
        "message": "댓글이 등록되었습니다.",
        "review": {
            "id": review_id,
            "user_id": user["id"],
            "user_nickname": nickname,
            "content": content,
            "created_at": created_at,
        },
    }


def anonymize_user_reviews(settings: Settings, user_id: str) -> None:
    try:
        with connect(settings) as connection:
            connection.execute(
                "UPDATE parking_reviews SET user_id = ?, user_nickname = ? WHERE user_id = ?",
                (WITHDRAWN_AUTHOR_ID, WITHDRAWN_AUTHOR_NAME, user_id),
            )
    except sqlite3.Error:
        # Reads mask deleted authors immediately; the next startup retries disk cleanup.
        logger.warning("Could not anonymize withdrawn reviews in storage")


def anonymize_orphaned_reviews(settings: Settings, accounts: Database) -> None:
    with accounts.connect(write=True), connect(settings) as connection:
        connection.execute("ATTACH DATABASE ? AS membership", (str(accounts.path),))
        connection.execute(
            """UPDATE parking_reviews SET user_id = ?, user_nickname = ?
               WHERE user_id != ? AND NOT EXISTS
               (SELECT 1 FROM membership.users u WHERE u.id = parking_reviews.user_id)""",
            (WITHDRAWN_AUTHOR_ID, WITHDRAWN_AUTHOR_NAME, WITHDRAWN_AUTHOR_ID),
        )
