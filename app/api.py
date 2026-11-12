"""Public HTTP endpoints."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.db import connect
from app.parking import get_lot, search_lots

router = APIRouter()


class RatingInput(BaseModel):
    score: int = Field(ge=1, le=5)


def _settings(request: Request):
    return request.app.state.settings


def _session_hash(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer 토큰이 필요합니다")
    token = authorization[7:]
    if len(token) != 64:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")
    return hashlib.sha256(token.encode()).hexdigest()


def _require_session(connection, token_hash: str) -> None:
    if connection.execute("SELECT 1 FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone() is None:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/realtime")
def realtime_health(request: Request) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with connect(_settings(request)) as connection:
        latest = connection.execute("SELECT MAX(fetched_at) FROM realtime_status").fetchone()[0]
        fresh_linked = connection.execute(
            """SELECT COUNT(*) FROM realtime_status s
               JOIN realtime_links l ON l.source_id = s.source_id
               WHERE s.fetched_at >= ?""",
            (cutoff,),
        ).fetchone()[0]
    return {
        "last_fetched_at": latest,
        "fresh_linked_lots": fresh_linked,
        "refresh_interval_seconds": _settings(request).realtime_refresh_seconds,
    }


@router.post("/sessions/anonymous", status_code=status.HTTP_201_CREATED)
def create_anonymous_session(request: Request) -> dict:
    token = secrets.token_hex(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with connect(_settings(request)) as connection:
        connection.execute(
            "INSERT INTO sessions(token_hash, created_at) VALUES (?, ?)",
            (token_hash, datetime.now(timezone.utc).isoformat()),
        )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/parking-lots")
def list_parking_lots(
    request: Request,
    name: str | None = Query(default=None, max_length=100),
    address: str | None = Query(default=None, max_length=150),
    fee: str | None = Query(default=None, pattern="^(free|paid|mixed)$"),
    accessible: bool | None = None,
    lat: float | None = Query(default=None, ge=-90, le=90),
    lon: float | None = Query(default=None, ge=-180, le=180),
    radius_m: int = Query(default=3000, ge=100, le=20000),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str | None = Query(default=None, pattern="^(name|price|rating|distance)$"),
) -> dict:
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail="lat와 lon은 함께 입력해야 합니다")
    if sort == "distance" and lat is None:
        raise HTTPException(status_code=422, detail="거리 순 정렬에는 lat와 lon이 필요합니다")
    with connect(_settings(request)) as connection:
        return search_lots(
            connection,
            name=name,
            address=address,
            fee=fee,
            accessible=accessible,
            latitude=lat,
            longitude=lon,
            radius_m=radius_m,
            page=page,
            page_size=page_size,
            sort=sort or ("distance" if lat is not None else "name"),
        )


@router.get("/parking-lots/{parking_id}")
def parking_lot_detail(parking_id: str, request: Request) -> dict:
    with connect(_settings(request)) as connection:
        lot = get_lot(connection, parking_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
    return lot


@router.get("/parking-lots/{parking_id}/congestion")
def parking_lot_congestion(parking_id: str, request: Request) -> dict:
    lot = parking_lot_detail(parking_id, request)
    return lot["congestion"]


@router.get("/favorites")
def list_favorites(request: Request, token_hash: str = Depends(_session_hash)) -> dict:
    with connect(_settings(request)) as connection:
        _require_session(connection, token_hash)
        ids = connection.execute(
            "SELECT parking_id FROM favorites WHERE token_hash = ? ORDER BY parking_id",
            (token_hash,),
        ).fetchall()
        items = [get_lot(connection, row["parking_id"]) for row in ids]
    return {"items": items}


@router.put("/favorites/{parking_id}")
def add_favorite(parking_id: str, request: Request, token_hash: str = Depends(_session_hash)) -> dict:
    with connect(_settings(request)) as connection:
        _require_session(connection, token_hash)
        if get_lot(connection, parking_id) is None:
            raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
        connection.execute(
            "INSERT OR IGNORE INTO favorites(token_hash, parking_id) VALUES (?, ?)",
            (token_hash, parking_id),
        )
    return {"parking_id": parking_id, "favorite": True}


@router.delete("/favorites/{parking_id}")
def remove_favorite(parking_id: str, request: Request, token_hash: str = Depends(_session_hash)) -> dict:
    with connect(_settings(request)) as connection:
        _require_session(connection, token_hash)
        connection.execute(
            "DELETE FROM favorites WHERE token_hash = ? AND parking_id = ?",
            (token_hash, parking_id),
        )
    return {"parking_id": parking_id, "favorite": False}


@router.put("/parking-lots/{parking_id}/rating")
def rate_parking_lot(
    parking_id: str,
    body: RatingInput,
    request: Request,
    token_hash: str = Depends(_session_hash),
) -> dict:
    with connect(_settings(request)) as connection:
        _require_session(connection, token_hash)
        if get_lot(connection, parking_id) is None:
            raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
        connection.execute(
            """INSERT INTO ratings(token_hash, parking_id, score) VALUES (?, ?, ?)
               ON CONFLICT(token_hash, parking_id) DO UPDATE SET score = excluded.score""",
            (token_hash, parking_id, body.score),
        )
        rating = get_lot(connection, parking_id)["rating"]
    return rating

