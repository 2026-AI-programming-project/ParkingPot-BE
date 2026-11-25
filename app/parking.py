"""Parking lot search and response formatting."""

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from app.geo import bounds, distance_m

REALTIME_STATUS_MAX_AGE = timedelta(minutes=10)


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _congestion(row) -> dict:
    fetched_at = row["fetched_at"]
    total = row["realtime_total"]
    available = row["available_spaces"]
    result = {
        "level": "unknown",
        "color": "gray",
        "available_percent": None,
        "available_spaces": None,
        "total_spaces": None,
        "fetched_at": fetched_at,
    }
    if fetched_at is None:
        return result
    try:
        age = datetime.now(UTC) - datetime.fromisoformat(fetched_at)
    except ValueError:
        return result
    if age > REALTIME_STATUS_MAX_AGE or age < -timedelta(minutes=1):
        return result
    if total is None or available is None or total <= 0 or not 0 <= available <= total:
        return result
    available_percent = available * 100 // total
    level, color = (
        ("available", "green")
        if available_percent >= 50
        else ("moderate", "yellow")
        if available_percent >= 20
        else ("busy", "red")
    )
    result.update(
        level=level,
        color=color,
        available_percent=available_percent,
        available_spaces=available,
        total_spaces=total,
    )
    return result


def serialize_lot(row, distance: float | None = None) -> dict:
    keywords = []
    if row["fee_type"] in {"free", "mixed"}:
        keywords.append("무료")
    if row["fee_type"] in {"paid", "mixed"}:
        keywords.append("유료")
    if row["accessible"] == 1:
        keywords.append("장애인 전용 주차구역")
    return {
        "id": row["id"],
        "data_source": "catalog",
        "name": row["name"],
        "address": row["address"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "fee_type": row["fee_type"],
        "keywords": keywords,
        "fee_description": row["fee_description"],
        "hourly_price": row["hourly_price"],
        "accessible": None if row["accessible"] is None else bool(row["accessible"]),
        "total_spaces": row["total_spaces"],
        "operating_days": row["operating_days"],
        "opening_hours": {
            "weekday": {"open": row["weekday_open"], "close": row["weekday_close"]},
            "saturday": {"open": row["saturday_open"], "close": row["saturday_close"]},
            "holiday": {"open": row["holiday_open"], "close": row["holiday_close"]},
        },
        "rating": {
            "average": None if row["rating_average"] is None else round(row["rating_average"], 2),
            "count": row["rating_count"],
        },
        "reference_date": row["reference_date"],
        "distance_m": None if distance is None else round(distance),
        "congestion": _congestion(row),
    }


SELECT_LOTS = """
SELECT p.*, s.total_spaces AS realtime_total, s.available_spaces, s.fetched_at
     , (SELECT AVG(score) FROM ratings WHERE parking_id = p.id) AS rating_average
     , (SELECT COUNT(*) FROM ratings WHERE parking_id = p.id) AS rating_count
FROM parking_lots p
LEFT JOIN realtime_links l ON l.parking_id = p.id
LEFT JOIN realtime_status s ON s.source_id = l.source_id
"""


def search_lots(
    connection,
    *,
    name: str | None,
    address: str | None,
    fee: str | None,
    accessible: bool | None,
    latitude: float | None,
    longitude: float | None,
    radius_m: int,
    page: int,
    page_size: int,
    sort: str,
) -> dict:
    from app.live_parking import search_live_facilities, serialize_live_lot

    filters = []
    params: list = []
    if name:
        filters.append("p.name LIKE ? ESCAPE '\\'")
        params.append(_like(name.strip()))
    if address:
        filters.append("p.address LIKE ? ESCAPE '\\'")
        params.append(_like(address.strip()))
    if fee:
        if fee == "mixed":
            filters.append("p.fee_type = ?")
            params.append(fee)
        else:
            filters.append("p.fee_type IN (?, 'mixed')")
            params.append(fee)
    if accessible is not None:
        filters.append("p.accessible = ?")
        params.append(int(accessible))
    nearby = latitude is not None and longitude is not None
    if nearby:
        min_lat, max_lat, min_lon, max_lon = bounds(latitude, longitude, radius_m)
        filters.extend(["p.latitude BETWEEN ? AND ?", "p.longitude BETWEEN ? AND ?"])
        params.extend([min_lat, max_lat, min_lon, max_lon])
    where = " WHERE " + " AND ".join(filters) if filters else ""
    entries: list[tuple[dict, float | None]] = []
    for row in connection.execute(SELECT_LOTS + where, params):
        distance = (
            distance_m(latitude, longitude, row["latitude"], row["longitude"]) if nearby else None
        )
        if distance is None or distance <= radius_m:
            entries.append((serialize_lot(row, distance), distance))

    # The realtime provider has its own IDs. Include unmatched facilities in the
    # normal search only when the requested filters do not require missing metadata.
    if not address and fee is None and accessible is None:
        live = search_live_facilities(
            connection,
            name=name,
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m,
            only_unlinked=True,
            page=1,
            page_size=None,
            fresh_only=False,
        )
        for marker in live["items"]:
            distance = (
                distance_m(latitude, longitude, marker["latitude"], marker["longitude"])
                if nearby
                else None
            )
            entries.append((serialize_live_lot(marker), distance))

    if sort == "price":
        entries.sort(
            key=lambda pair: (
                pair[0]["hourly_price"] is None,
                pair[0]["hourly_price"] or 0,
                pair[1] if pair[1] is not None else 0,
                pair[0]["id"],
            )
        )
    elif sort == "rating":
        entries.sort(
            key=lambda pair: (
                pair[0]["rating"]["average"] is None,
                -(pair[0]["rating"]["average"] or 0),
                pair[1] if pair[1] is not None else 0,
                pair[0]["id"],
            )
        )
    elif sort == "name":
        entries.sort(key=lambda pair: (pair[0]["name"].casefold(), pair[0]["id"]))
    else:
        entries.sort(key=lambda pair: (pair[1], pair[0]["id"]))

    total = len(entries)
    nearest_id = (
        min(entries, key=lambda pair: (pair[1], pair[0]["id"]))[0]["id"]
        if nearby and entries
        else None
    )
    items = [item for item, _ in entries[(page - 1) * page_size : page * page_size]]
    for item in items:
        item["is_nearest"] = item["id"] == nearest_id if nearest_id else False
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "nearest_id": nearest_id,
    }


def get_lot_location(connection, parking_id: str):
    if parking_id.startswith("realtime:"):
        from app.live_parking import get_live_lot

        return get_live_lot(connection, parking_id.removeprefix("realtime:"))
    return connection.execute(
        "SELECT name, latitude, longitude FROM parking_lots WHERE id = ?", (parking_id,)
    ).fetchone()


def kakao_directions_urls(lot, origin: tuple[float, float] | None) -> tuple[str | None, str]:
    destination = f"{lot['latitude']},{lot['longitude']}"
    destination_name = quote(lot["name"], safe="")
    destination_path = f"{destination_name},{destination}"
    if origin is None:
        return None, f"https://map.kakao.com/link/to/{destination_path}"

    start = f"{origin[0]},{origin[1]}"
    app_url = f"kakaomap://route?sp={start}&ep={destination}&by=car"
    start_name = quote("출발지", safe="")
    web_url = f"https://map.kakao.com/link/by/car/{start_name},{start}/{destination_path}"
    return app_url, web_url


def get_lot(connection, parking_id: str) -> dict | None:
    if parking_id.startswith("realtime:"):
        from app.live_parking import get_live_lot

        return get_live_lot(connection, parking_id.removeprefix("realtime:"))
    row = connection.execute(SELECT_LOTS + " WHERE p.id = ?", (parking_id,)).fetchone()
    return serialize_lot(row) if row else None
