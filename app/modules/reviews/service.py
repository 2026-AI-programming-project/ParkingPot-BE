"""Review persistence and business rules."""

import secrets
from datetime import UTC, datetime

from fastapi import HTTPException

from app.config import Settings
from app.parking import get_lot
from app.parking_db import connect


def list_reviews(settings: Settings, parking_id: str) -> dict:
    with connect(settings) as connection:
        if get_lot(connection, parking_id) is None:
            raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
        rows = connection.execute(
            """SELECT id, user_id, user_nickname, content, created_at
               FROM parking_reviews WHERE parking_id = ?
               ORDER BY created_at DESC, id DESC""",
            (parking_id,),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows)}


def create_review(settings: Settings, parking_id: str, user: dict, content: str) -> dict:
    content = content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="리뷰 내용을 입력해 주세요")
    created_at = datetime.now(UTC).isoformat()
    review_id = secrets.token_hex(16)
    with connect(settings) as connection:
        if get_lot(connection, parking_id) is None:
            raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
        connection.execute(
            """INSERT INTO parking_reviews
               (id, parking_id, user_id, user_nickname, content, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (review_id, parking_id, user["id"], user["nickname"], content, created_at),
        )
    return {
        "message": "댓글이 등록되었습니다.",
        "review": {
            "id": review_id,
            "user_id": user["id"],
            "user_nickname": user["nickname"],
            "content": content,
            "created_at": created_at,
        },
    }
