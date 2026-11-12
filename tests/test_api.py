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
