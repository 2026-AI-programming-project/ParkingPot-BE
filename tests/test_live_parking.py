from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.parking_db import connect
from app.sync import _save_page, link_facilities


def test_live_map_shows_unlinked_facilities_and_excludes_stale_data(tmp_path):
    settings = Settings(database_path=str(tmp_path / "parking.db"))
    with TestClient(create_app(parking_settings=settings)) as client:
        with connect(settings) as connection:
            _save_page(
                connection,
                "catalog",
                [
                    {
                        "prkplceNo": "A",
                        "prkplceNm": "연결 주차장",
                        "latitude": "37.5",
                        "longitude": "127",
                    }
                ],
            )
            _save_page(
                connection,
                "facility",
                [
                    {
                        "prk_center_id": "RT-A",
                        "prk_plce_nm": "연결 주차장",
                        "prk_plce_entrc_la": "37.5",
                        "prk_plce_entrc_lo": "127",
                    },
                    {
                        "prk_center_id": "RT-B",
                        "prk_plce_nm": "별도 주차장",
                        "prk_plce_entrc_la": "37.5005",
                        "prk_plce_entrc_lo": "127",
                    },
                    {"prk_center_id": "RT-C", "prk_plce_nm": "좌표 없음"},
                ],
            )
            _save_page(
                connection,
                "realtime",
                [
                    {
                        "prk_center_id": "RT-A",
                        "pkfc_ParkingLots_total": "10",
                        "pkfc_Available_ParkingLots_total": "8",
                    },
                    {
                        "prk_center_id": "RT-B",
                        "pkfc_ParkingLots_total": "10",
                        "pkfc_Available_ParkingLots_total": "2",
                    },
                    {
                        "prk_center_id": "RT-C",
                        "pkfc_ParkingLots_total": "10",
                        "pkfc_Available_ParkingLots_total": "5",
                    },
                ],
            )
        assert link_facilities(settings) == 1

        response = client.get(
            "/realtime-parking-lots",
            params={"lat": 37.5, "lon": 127, "radius_m": 200},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        by_source = {item["source_id"]: item for item in body["items"]}
        assert by_source["RT-A"]["parking_id"] == "A"
        assert by_source["RT-A"]["congestion"]["color"] == "green"
        assert by_source["RT-B"]["parking_id"] is None
        assert by_source["RT-B"]["congestion"]["color"] == "yellow"
        assert by_source["RT-B"]["distance_m"] > 0
        assert (
            client.get("/realtime-parking-lots", params={"only_unlinked": True}).json()["total"]
            == 1
        )
        assert client.get("/realtime-parking-lots", params={"lat": 37.5}).status_code == 422
        assert client.get("/health/realtime").json()["fresh_mappable_facilities"] == 2

        search = client.get("/parking-lots", params={"name": "별도 주차장"})
        assert search.status_code == 200
        assert search.json()["total"] == 1
        lot = search.json()["items"][0]
        assert lot["id"] == "realtime:RT-B"
        assert lot["data_source"] == "realtime_facility"
        assert lot["address"] == ""
        assert lot["hourly_price"] is None
        assert lot["congestion"]["color"] == "yellow"
        assert client.get("/parking-lots/realtime:RT-B").json()["name"] == "별도 주차장"
        assert client.get("/parking-lots/realtime:RT-B/congestion").json()["color"] == "yellow"
        assert client.get("/parking-lots/realtime:RT-B/directions").status_code == 200
        assert (
            client.get("/parking-lots", params={"name": "별도 주차장", "fee": "free"}).json()[
                "total"
            ]
            == 0
        )

        nearby = client.get(
            "/parking-lots",
            params={"lat": 37.5, "lon": 127, "radius_m": 200, "page_size": 1, "page": 2},
        ).json()
        assert nearby["total"] == 2
        assert nearby["nearest_id"] == "A"
        assert nearby["items"][0]["id"] == "realtime:RT-B"

        token = client.post("/sessions/anonymous").json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.put("/favorites/realtime:RT-B", headers=headers).status_code == 409
        assert (
            client.put(
                "/parking-lots/realtime:RT-B/rating",
                json={"score": 5},
                headers=headers,
            ).status_code
            == 409
        )

        with connect(settings) as connection:
            connection.execute(
                "UPDATE realtime_status SET fetched_at = ? WHERE source_id = 'RT-B'",
                ("2020-01-01T00:00:00+00:00",),
            )
        assert client.get("/realtime-parking-lots").json()["total"] == 1
        stale_lot = client.get("/parking-lots", params={"name": "별도 주차장"}).json()
        assert stale_lot["total"] == 1
        assert stale_lot["items"][0]["congestion"]["level"] == "unknown"
