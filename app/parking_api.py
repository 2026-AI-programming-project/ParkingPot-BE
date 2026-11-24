"""Public HTTP endpoints."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.parking import (
    REALTIME_STATUS_MAX_AGE,
    get_lot,
    get_lot_location,
    kakao_directions_urls,
    search_lots,
)
from app.parking_db import connect
from app.geocoding import resolve_location
from app.parking_schemas import (
    AnonymousSession,
    FavoriteList,
    FavoriteResult,
    ParkingCongestion,
    ParkingLot,
    ParkingRating,
    ParkingSearchResult,
    RealtimeHealth,
)

router = APIRouter()

# Keep the only credentials to saved items and the original votes usable across upgrades.
EMPTY_SESSION = """
    NOT EXISTS (SELECT 1 FROM favorites WHERE favorites.token_hash = sessions.token_hash)
    AND NOT EXISTS (SELECT 1 FROM ratings WHERE ratings.token_hash = sessions.token_hash)
"""


class RatingInput(BaseModel):
    score: int = Field(ge=1, le=5)


class ParkingDestination(BaseModel):
    name: str
    latitude: float
    longitude: float


class ParkingDirections(BaseModel):
    parking_id: str
    destination: ParkingDestination
    app_url: str | None = Field(
        description="출발 좌표를 전달했을 때 카카오맵 앱에서 열 자동차 길찾기 링크"
    )
    web_url: str = Field(
        description="카카오맵 앱을 열 수 없거나 출발 좌표가 없을 때 사용할 웹 링크"
    )


def _settings(request: Request):
    return request.app.state.settings


def _session_hash(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer 토큰이 필요합니다")
    token = authorization[7:]
    if len(token) != 64:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")
    return hashlib.sha256(token.encode()).hexdigest()


def _require_session(connection, token_hash: str, idle_days: int) -> None:
    now = datetime.now(UTC)
    cutoff = (now - timedelta(days=idle_days)).isoformat() if idle_days else None
    updated = connection.execute(
        f"""UPDATE sessions SET last_seen_at = ?
            WHERE token_hash = ? AND (? IS NULL OR last_seen_at > ? OR NOT ({EMPTY_SESSION}))""",
        (now.isoformat(), token_hash, cutoff, cutoff),
    )
    if not updated.rowcount:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")


@router.get("/health/realtime", response_model=RealtimeHealth)
def realtime_health(request: Request) -> dict:
    cutoff = (datetime.now(UTC) - REALTIME_STATUS_MAX_AGE).isoformat()
    with connect(_settings(request)) as connection:
        latest = connection.execute("SELECT MAX(fetched_at) FROM realtime_status").fetchone()[0]
        total_lots = connection.execute("SELECT COUNT(*) FROM parking_lots").fetchone()[0]
        linked_lots = connection.execute("SELECT COUNT(*) FROM realtime_links").fetchone()[0]
        source_statuses = connection.execute("SELECT COUNT(*) FROM realtime_status").fetchone()[0]
        source_statuses_with_facility = connection.execute(
            """SELECT COUNT(*) FROM realtime_status s
               JOIN realtime_facilities f ON f.source_id = s.source_id"""
        ).fetchone()[0]
        fresh_linked = connection.execute(
            """SELECT COUNT(*) FROM realtime_status s
               JOIN realtime_links l ON l.source_id = s.source_id
               WHERE s.fetched_at >= ?""",
            (cutoff,),
        ).fetchone()[0]
    return {
        "last_fetched_at": latest,
        "total_lots": total_lots,
        "linked_lots": linked_lots,
        "source_statuses": source_statuses,
        "source_statuses_with_facility": source_statuses_with_facility,
        "fresh_linked_lots": fresh_linked,
        "refresh_interval_seconds": _settings(request).realtime_refresh_seconds,
    }


@router.post(
    "/sessions/anonymous", response_model=AnonymousSession, status_code=status.HTTP_201_CREATED
)
def create_anonymous_session(request: Request) -> dict:
    token = secrets.token_hex(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    settings = _settings(request)
    now = datetime.now(UTC)
    with connect(settings, write=True) as connection:
        if settings.anonymous_idle_days:
            cutoff = (now - timedelta(days=settings.anonymous_idle_days)).isoformat()
            connection.execute(
                f"DELETE FROM sessions WHERE last_seen_at <= ? AND {EMPTY_SESSION}",
                (cutoff,),
            )
        connection.execute(
            "INSERT INTO sessions(token_hash, created_at, last_seen_at) VALUES (?, ?, ?)",
            (token_hash, now.isoformat(), now.isoformat()),
        )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/parking-lots", response_model=ParkingSearchResult)
def list_parking_lots(
    request: Request,
    name: str | None = Query(default=None, max_length=100),
    address: str | None = Query(default=None, max_length=150),
    fee: str | None = Query(default=None, pattern="^(free|paid|mixed)$"),
    accessible: bool | None = None,
    lat: float | None = Query(default=None, ge=-90, le=90),
    lon: float | None = Query(default=None, ge=-180, le=180),
    radius_m: int = Query(default=500, ge=100, le=20000),
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


@router.get("/parking-lots/nearby", response_model=ParkingSearchResult)
def nearby_parking_lots(
    request: Request,
    address: str = Query(min_length=1, max_length=150),
    radius_m: int = Query(default=500, ge=100, le=20000),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Geocode an address, then return parking lots within the requested radius."""
    latitude, longitude = resolve_location(
        address.strip(), _settings(request).kakao_rest_api_key
    )
    with connect(_settings(request)) as connection:
        result = search_lots(
            connection,
            name=None,
            address=None,
            fee=None,
            accessible=None,
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m,
            page=page,
            page_size=page_size,
            sort="distance",
        )
    result["search_center"] = {"latitude": latitude, "longitude": longitude}
    return result


@router.get("/parking-lots/{parking_id}", response_model=ParkingLot)
def parking_lot_detail(parking_id: str, request: Request) -> dict:
    with connect(_settings(request)) as connection:
        lot = get_lot(connection, parking_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
    return lot


@router.get(
    "/parking-lots/{parking_id}/directions",
    response_model=ParkingDirections,
    summary="카카오맵 주차장 길찾기",
    responses={
        404: {"description": "주차장 없음"},
        409: {"description": "유효한 목적지 좌표 없음"},
    },
)
def parking_lot_directions(
    parking_id: str,
    request: Request,
    lat: float | None = Query(default=None, ge=-90, le=90, description="출발지 위도 (WGS84)"),
    lon: float | None = Query(default=None, ge=-180, le=180, description="출발지 경도 (WGS84)"),
) -> ParkingDirections:
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail="lat와 lon은 함께 입력해야 합니다")

    with connect(_settings(request)) as connection:
        lot = get_lot_location(connection, parking_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
    latitude, longitude = lot["latitude"], lot["longitude"]
    if not (
        isinstance(latitude, (float, int))
        and isinstance(longitude, (float, int))
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
        and (latitude, longitude) != (0, 0)
    ):
        raise HTTPException(status_code=409, detail="주차장 길찾기에 사용할 좌표가 없습니다")

    origin = (lat, lon) if lat is not None and lon is not None else None
    app_url, web_url = kakao_directions_urls(lot, origin)
    return ParkingDirections(
        parking_id=parking_id,
        destination=ParkingDestination(name=lot["name"], latitude=latitude, longitude=longitude),
        app_url=app_url,
        web_url=web_url,
    )


@router.get("/parking-lots/{parking_id}/congestion", response_model=ParkingCongestion)
def parking_lot_congestion(parking_id: str, request: Request) -> dict:
    lot = parking_lot_detail(parking_id, request)
    return lot["congestion"]


@router.get("/favorites", response_model=FavoriteList)
def list_favorites(request: Request, token_hash: str = Depends(_session_hash)) -> dict:
    settings = _settings(request)
    with connect(settings, write=True) as connection:
        _require_session(connection, token_hash, settings.anonymous_idle_days)
        ids = connection.execute(
            "SELECT parking_id FROM favorites WHERE token_hash = ? ORDER BY parking_id",
            (token_hash,),
        ).fetchall()
        items = [get_lot(connection, row["parking_id"]) for row in ids]
    return {"items": items}


@router.put("/favorites/{parking_id}", response_model=FavoriteResult)
def add_favorite(
    parking_id: str, request: Request, token_hash: str = Depends(_session_hash)
) -> dict:
    settings = _settings(request)
    with connect(settings, write=True) as connection:
        _require_session(connection, token_hash, settings.anonymous_idle_days)
        if get_lot(connection, parking_id) is None:
            raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
        connection.execute(
            "INSERT OR IGNORE INTO favorites(token_hash, parking_id) VALUES (?, ?)",
            (token_hash, parking_id),
        )
    return {"parking_id": parking_id, "favorite": True}


@router.delete("/favorites/{parking_id}", response_model=FavoriteResult)
def remove_favorite(
    parking_id: str, request: Request, token_hash: str = Depends(_session_hash)
) -> dict:
    settings = _settings(request)
    with connect(settings, write=True) as connection:
        _require_session(connection, token_hash, settings.anonymous_idle_days)
        connection.execute(
            "DELETE FROM favorites WHERE token_hash = ? AND parking_id = ?",
            (token_hash, parking_id),
        )
    return {"parking_id": parking_id, "favorite": False}


@router.put("/parking-lots/{parking_id}/rating", response_model=ParkingRating)
def rate_parking_lot(
    parking_id: str,
    body: RatingInput,
    request: Request,
    token_hash: str = Depends(_session_hash),
) -> dict:
    settings = _settings(request)
    with connect(settings, write=True) as connection:
        _require_session(connection, token_hash, settings.anonymous_idle_days)
        if get_lot(connection, parking_id) is None:
            raise HTTPException(status_code=404, detail="주차장을 찾을 수 없습니다")
        connection.execute(
            """INSERT INTO ratings(token_hash, parking_id, score) VALUES (?, ?, ?)
               ON CONFLICT(token_hash, parking_id) DO UPDATE SET score = excluded.score""",
            (token_hash, parking_id, body.score),
        )
        rating = get_lot(connection, parking_id)["rating"]
    return rating
