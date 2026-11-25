"""Map markers from the provider's facility and current availability data."""

from datetime import UTC, datetime, timedelta

from app.geo import bounds, distance_m
from app.parking import REALTIME_STATUS_MAX_AGE, _congestion, _like


def search_live_facilities(
    connection,
    *,
    name: str | None,
    latitude: float | None,
    longitude: float | None,
    radius_m: int,
    only_unlinked: bool,
    page: int,
    page_size: int | None,
    fresh_only: bool = True,
) -> dict:
    """Return positioned facilities with status, including unlinked ones."""
    now = datetime.now(UTC)
    cutoff = (now - REALTIME_STATUS_MAX_AGE).isoformat()
    future_limit = (now + timedelta(minutes=1)).isoformat()
    filters = [
        "f.latitude BETWEEN -90 AND 90",
        "f.longitude BETWEEN -180 AND 180",
        "f.latitude != 0",
        "f.longitude != 0",
    ]
    params: list = []
    if fresh_only:
        filters.extend(["s.fetched_at >= ?", "s.fetched_at <= ?"])
        params.extend([cutoff, future_limit])
    if name:
        filters.append("f.name LIKE ? ESCAPE '\\'")
        params.append(_like(name.strip()))
    if only_unlinked:
        filters.append("l.parking_id IS NULL")
    nearby = latitude is not None and longitude is not None
    if nearby:
        min_lat, max_lat, min_lon, max_lon = bounds(latitude, longitude, radius_m)
        filters.extend(["f.latitude BETWEEN ? AND ?", "f.longitude BETWEEN ? AND ?"])
        params.extend([min_lat, max_lat, min_lon, max_lon])
    rows = connection.execute(
        """SELECT f.source_id, f.name, f.latitude, f.longitude,
                  l.parking_id, s.total_spaces AS realtime_total,
                  s.available_spaces, s.fetched_at
           FROM realtime_facilities f
           JOIN realtime_status s ON s.source_id = f.source_id
           LEFT JOIN realtime_links l ON l.source_id = f.source_id
           WHERE """
        + " AND ".join(filters),
        params,
    ).fetchall()
    located = []
    for row in rows:
        distance = (
            distance_m(latitude, longitude, row["latitude"], row["longitude"]) if nearby else None
        )
        if distance is None or distance <= radius_m:
            located.append((row, distance))
    located.sort(
        key=lambda item: (
            item[1] if item[1] is not None else float("inf"),
            item[0]["name"],
            item[0]["source_id"],
        )
    )
    total = len(located)
    items = []
    page_rows = (
        located[(page - 1) * page_size : page * page_size] if page_size is not None else located
    )
    for row, distance in page_rows:
        items.append(
            {
                "source_id": row["source_id"],
                "parking_id": row["parking_id"],
                "name": row["name"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "distance_m": round(distance) if distance is not None else None,
                "congestion": _congestion(row),
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def serialize_live_lot(marker: dict) -> dict:
    """Use the search response shape without inventing unknown catalog fields."""
    congestion = marker["congestion"]
    return {
        "id": f"realtime:{marker['source_id']}",
        "source_id": marker["source_id"],
        "data_source": "realtime_facility",
        "name": marker["name"],
        "address": "",
        "latitude": marker["latitude"],
        "longitude": marker["longitude"],
        "fee_type": "unknown",
        "keywords": [],
        "fee_description": "",
        "hourly_price": None,
        "accessible": None,
        "total_spaces": congestion["total_spaces"],
        "operating_days": None,
        "opening_hours": {
            "weekday": {"open": None, "close": None},
            "saturday": {"open": None, "close": None},
            "holiday": {"open": None, "close": None},
        },
        "rating": {"average": None, "count": 0},
        "reference_date": None,
        "distance_m": marker["distance_m"],
        "congestion": congestion,
    }


def get_live_lot(connection, source_id: str) -> dict | None:
    row = connection.execute(
        """SELECT f.source_id, f.name, f.latitude, f.longitude,
                  s.total_spaces AS realtime_total, s.available_spaces, s.fetched_at
           FROM realtime_facilities f
           JOIN realtime_status s ON s.source_id = f.source_id
           LEFT JOIN realtime_links l ON l.source_id = f.source_id
           WHERE f.source_id = ? AND l.parking_id IS NULL""",
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    return serialize_live_lot(
        {
            "source_id": row["source_id"],
            "name": row["name"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "distance_m": None,
            "congestion": _congestion(row),
        }
    )
