"""Parking lot search and response formatting."""

from datetime import UTC, datetime, timedelta

from app.geo import bounds, distance_m


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
    if age > timedelta(minutes=10) or age < -timedelta(minutes=1):
        return result
    if total is None or available is None or total <= 0 or not 0 <= available <= total:
        return result
    ratio = available / total
    level, color = (
        ("available", "green")
        if ratio >= 0.5
        else ("moderate", "yellow")
        if ratio >= 0.2
        else ("busy", "red")
    )
    result.update(
        level=level,
        color=color,
        available_percent=round(ratio * 100),
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
    if nearby:
        rows = connection.execute(SELECT_LOTS + where, params).fetchall()
        located = [
            (row, distance_m(latitude, longitude, row["latitude"], row["longitude"]))
            for row in rows
        ]
        located = [(row, distance) for row, distance in located if distance <= radius_m]
        if sort == "price":
            located.sort(
                key=lambda item: (
                    item[0]["hourly_price"] is None,
                    item[0]["hourly_price"] or 0,
                    item[1],
                )
            )
        elif sort == "rating":
            located.sort(
                key=lambda item: (
                    item[0]["rating_average"] is None,
                    -(item[0]["rating_average"] or 0),
                    item[1],
                )
            )
        elif sort == "name":
            located.sort(key=lambda item: (item[0]["name"], item[0]["id"]))
        else:
            located.sort(key=lambda item: (item[1], item[0]["id"]))
        total = len(located)
        nearest_id = min(located, key=lambda item: item[1])[0]["id"] if located else None
        page_rows = located[(page - 1) * page_size : page * page_size]
        items = [serialize_lot(row, distance) for row, distance in page_rows]
    else:
        total = connection.execute(
            "SELECT COUNT(*) FROM parking_lots p" + where, params
        ).fetchone()[0]
        order = {
            "name": "p.name, p.id",
            "price": "p.hourly_price IS NULL, p.hourly_price, p.id",
            "rating": "rating_average IS NULL, rating_average DESC, p.id",
        }[sort]
        rows = connection.execute(
            SELECT_LOTS + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        items = [serialize_lot(row) for row in rows]
        nearest_id = None
    for item in items:
        item["is_nearest"] = item["id"] == nearest_id if nearest_id else False
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "nearest_id": nearest_id,
    }


def get_lot(connection, parking_id: str) -> dict | None:
    row = connection.execute(SELECT_LOTS + " WHERE p.id = ?", (parking_id,)).fetchone()
    return serialize_lot(row) if row else None
