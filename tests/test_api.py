from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.parking_db import connect
from app.sync import _save_page, link_facilities


def _catalog(parking_id, name, lat, lon, fee, charge):
    return {
        "prkplceNo": parking_id,
        "prkplceNm": name,
        "rdnmadr": "서울특별시 중구 세종대로 1",
        "latitude": lat,
        "longitude": lon,
        "parkingchrgeInfo": fee,
        "basicTime": "30",
        "basicCharge": charge,
        "addUnitTime": "10",
        "addUnitCharge": "500",
        "pwdbsPpkZoneYn": "Y",
        "weekdayOperOpenHhmm": "09:00",
        "weekdayOperColseHhmm": "18:00",
    }


def test_search_nearby_congestion_favorites_and_ratings(tmp_path):
    settings = Settings(database_path=str(tmp_path / "test.db"))
    with TestClient(create_app(parking_settings=settings)) as client:
        with connect(settings) as connection:
            _save_page(
                connection,
                "catalog",
                [
                    _catalog("A", "시청 주차장", "37.5665", "126.9780", "유료", "1000"),
                    _catalog("B", "먼 주차장", "37.5800", "126.9900", "무료", "0"),
                ],
            )
            _save_page(
                connection,
                "facility",
                [
                    {
                        "prk_center_id": "RT-A",
                        "prk_plce_nm": "시청 주차장",
                        "prk_plce_entrc_la": "37.5665",
                        "prk_plce_entrc_lo": "126.9780",
                    }
                ],
            )
            _save_page(
                connection,
                "realtime",
                [
                    {
                        "prk_center_id": "RT-A",
                        "pkfc_ParkingLots_total": "100",
                        "pkfc_Available_ParkingLots_total": "15",
                    }
                ],
            )
        assert link_facilities(settings) == 1

        response = client.get(
            "/parking-lots",
            params={
                "name": "시청",
                "lat": 37.5665,
                "lon": 126.978,
                "radius_m": 500,
                "fee": "paid",
                "accessible": True,
            },
        )
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        listing = response.json()
        assert listing["total"] == 1
        assert listing["nearest_id"] == "A"
        assert listing["items"][0]["is_nearest"] is True
        assert listing["items"][0]["congestion"]["level"] == "busy"
        assert listing["items"][0]["congestion"]["color"] == "red"
        assert listing["items"][0]["congestion"]["available_percent"] == 15

        with connect(settings) as connection:
            connection.execute(
                "UPDATE realtime_status SET available_spaces = 20 WHERE source_id = 'RT-A'"
            )
        assert client.get("/parking-lots/A/congestion").json()["color"] == "yellow"
        with connect(settings) as connection:
            connection.execute(
                "UPDATE realtime_status SET available_spaces = 50 WHERE source_id = 'RT-A'"
            )
        assert client.get("/parking-lots/A/congestion").json()["color"] == "green"

        detail = client.get("/parking-lots/A").json()
        assert detail["hourly_price"] == 2500
        assert detail["opening_hours"]["weekday"] == {"open": "09:00", "close": "18:00"}
        assert (
            client.get("/parking-lots", params={"address": "세종대로", "fee": "free"}).json()[
                "total"
            ]
            == 1
        )
        assert client.get("/parking-lots", params={"sort": "price"}).json()["items"][0]["id"] == "B"
        assert client.get("/parking-lots", params={"sort": "distance"}).status_code == 422

        token = client.post("/sessions/anonymous").json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.put("/favorites/A", headers=headers).status_code == 200
        assert [
            item["id"] for item in client.get("/favorites", headers=headers).json()["items"]
        ] == ["A"]
        assert client.put("/parking-lots/A/rating", json={"score": 5}, headers=headers).json() == {
            "average": 5.0,
            "count": 1,
        }
        assert (
            client.get("/parking-lots", params={"sort": "rating"}).json()["items"][0]["id"] == "A"
        )
        assert client.delete("/favorites/A", headers=headers).status_code == 200
        assert client.get("/favorites", headers=headers).json()["items"] == []
        assert client.get("/favorites").status_code == 401


def test_directions_use_selected_parking_lot_and_optional_origin(tmp_path):
    settings = Settings(database_path=str(tmp_path / "test.db"))
    name = "시청 / 주차장 #1"
    with TestClient(create_app(parking_settings=settings)) as client:
        with connect(settings) as connection:
            _save_page(
                connection,
                "catalog",
                [
                    _catalog("A", "다른 주차장", "35.1", "129.1", "무료", "0"),
                    _catalog("B", name, "37.5665", "126.9780", "유료", "1000"),
                ],
            )

        response = client.get("/parking-lots/B/directions", params={"lat": 37.55, "lon": 126.99})
        assert response.status_code == 200
        route = response.json()
        assert route["parking_id"] == "B"
        assert route["destination"] == {"name": name, "latitude": 37.5665, "longitude": 126.978}
        app_url = urlsplit(route["app_url"])
        assert (app_url.scheme, app_url.netloc) == ("kakaomap", "route")
        assert parse_qs(app_url.query) == {
            "sp": ["37.55,126.99"],
            "ep": ["37.5665,126.978"],
            "by": ["car"],
        }
        web_url = urlsplit(route["web_url"])
        assert (web_url.scheme, web_url.netloc) == ("https", "map.kakao.com")
        prefix, departure, destination = web_url.path.rsplit("/", 2)
        assert prefix == "/link/by/car"
        assert unquote(departure) == "출발지,37.55,126.99"
        assert unquote(destination) == f"{name},37.5665,126.978"
        assert not web_url.query and not web_url.fragment

        response = client.get("/parking-lots/B/directions")
        assert response.status_code == 200
        assert response.json()["app_url"] is None
        web_path = urlsplit(response.json()["web_url"]).path
        assert web_path.startswith("/link/to/")
        assert unquote(web_path.removeprefix("/link/to/")) == f"{name},37.5665,126.978"


def test_directions_reject_missing_parking_coordinates_and_invalid_origin(tmp_path):
    settings = Settings(database_path=str(tmp_path / "test.db"))
    with TestClient(create_app(parking_settings=settings)) as client:
        with connect(settings) as connection:
            connection.executemany(
                "INSERT INTO parking_lots(id, name, latitude, longitude) VALUES (?, ?, ?, ?)",
                [("A", "좌표 없음", None, None), ("B", "범위 오류", 37, 181), ("C", "미상", 0, 0)],
            )

        assert client.get("/parking-lots/unknown/directions").status_code == 404
        for parking_id in ("A", "B", "C"):
            assert client.get(f"/parking-lots/{parking_id}/directions").status_code == 409
        for params in ({"lat": 37}, {"lon": 127}, {"lat": 91, "lon": 127}):
            assert client.get("/parking-lots/A/directions", params=params).status_code == 422


def test_stale_congestion_is_unknown(tmp_path):
    settings = Settings(database_path=str(tmp_path / "test.db"))
    with TestClient(create_app(parking_settings=settings)) as client:
        with connect(settings) as connection:
            _save_page(
                connection,
                "catalog",
                [_catalog("A", "시청 주차장", "37.5665", "126.9780", "유료", "1000")],
            )
            _save_page(
                connection,
                "facility",
                [
                    {
                        "prk_center_id": "A",
                        "prk_plce_nm": "시청 주차장",
                    }
                ],
            )
            _save_page(
                connection,
                "realtime",
                [
                    {
                        "prk_center_id": "A",
                        "pkfc_ParkingLots_total": "100",
                        "pkfc_Available_ParkingLots_total": "80",
                    }
                ],
            )
        link_facilities(settings)
        with connect(settings) as connection:
            connection.execute(
                "UPDATE realtime_status SET fetched_at = ?", ("2020-01-01T00:00:00+00:00",)
            )
        assert client.get("/parking-lots/A/congestion").json()["level"] == "unknown"


def test_congestion_display_percentage_and_color_agree_at_thresholds(tmp_path):
    settings = Settings(database_path=str(tmp_path / "test.db"))
    with TestClient(create_app(parking_settings=settings)) as client:
        with connect(settings) as connection:
            connection.execute("INSERT INTO parking_lots (id, name) VALUES ('A', '시청 주차장')")
            connection.execute(
                "INSERT INTO realtime_facilities (source_id, name) VALUES ('RT-A', '시청 주차장')"
            )
            connection.execute("INSERT INTO realtime_links VALUES ('A', 'RT-A')")
            connection.execute(
                "INSERT INTO realtime_status VALUES ('RT-A', 1000, 0, ?)",
                (datetime.now(UTC).isoformat(),),
            )
        for available, percent, color in (
            (199, 19, "red"),
            (200, 20, "yellow"),
            (499, 49, "yellow"),
            (500, 50, "green"),
        ):
            with connect(settings) as connection:
                connection.execute("UPDATE realtime_status SET available_spaces = ?", (available,))
            response = client.get("/parking-lots/A/congestion")
            assert response.status_code == 200
            assert response.json()["available_percent"] == percent
            assert response.json()["color"] == color
