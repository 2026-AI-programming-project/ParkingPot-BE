import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.parking_api import realtime_health
from app.parking_db import connect, init_db
from app.realtime import RealtimeRefresher, refresh_realtime_once
from app.sources import SourceError
from app.sync import _save_page, link_facilities


def _status(available: int) -> dict:
    return {
        "prk_center_id": "RT-A",
        "pkfc_ParkingLots_total": "10",
        "pkfc_Available_ParkingLots_total": str(available),
    }


def test_failed_refresh_keeps_previous_snapshot(tmp_path, monkeypatch):
    settings = Settings(database_path=str(tmp_path / "realtime.db"), status_service_key="test-key")
    init_db(settings)
    with connect(settings) as connection:
        _save_page(connection, "realtime", [_status(2)])

    def fetch(client, config, kind, page, page_size, **kwargs):
        if page == 1:
            return [_status(8)], 1001
        raise SourceError("second page failed")

    monkeypatch.setattr("app.realtime.fetch_page", fetch)
    with pytest.raises(SourceError, match="second page failed"):
        refresh_realtime_once(settings)
    with connect(settings) as connection:
        assert (
            connection.execute(
                "SELECT available_spaces FROM realtime_status WHERE source_id = 'RT-A'"
            ).fetchone()[0]
            == 2
        )


def test_successful_refresh_saves_all_pages(tmp_path, monkeypatch):
    settings = Settings(
        database_path=str(tmp_path / "realtime.db"),
        status_service_key="test-key",
        realtime_sido_code="11",
        realtime_sigungu_code="11110",
    )
    init_db(settings)
    pages = []

    def fetch(client, config, kind, page, page_size, **kwargs):
        pages.append((page, kwargs))
        item = _status(2 if page == 1 else 8)
        item["prk_center_id"] = f"RT-{page}"
        return [item], 1001

    monkeypatch.setattr("app.realtime.fetch_page", fetch)
    assert refresh_realtime_once(settings) == 2
    assert [page for page, _ in pages] == [1, 2]
    assert pages[0][1] == {"sido_code": "11", "sigungu_code": "11110"}
    with connect(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM realtime_status").fetchone()[0] == 2


def test_refresher_repairs_links_when_status_arrives(tmp_path, monkeypatch):
    settings = Settings(database_path=str(tmp_path / "links.db"), status_service_key="test-key")
    init_db(settings)
    with connect(settings) as connection:
        _save_page(
            connection,
            "catalog",
            [
                {
                    "prkplceNo": "A",
                    "prkplceNm": "시청 주차장",
                    "latitude": "37.5",
                    "longitude": "127.0",
                }
            ],
        )
        _save_page(
            connection,
            "facility",
            [
                {
                    "prk_center_id": "RT-A",
                    "prk_plce_nm": "시청 주차장",
                    "prk_plce_entrc_la": "37.5",
                    "prk_plce_entrc_lo": "127.0",
                }
            ],
        )
    linked = threading.Event()

    def fake_refresh(config, stop):
        with connect(config) as connection:
            _save_page(connection, "realtime", [_status(8)])
        return 1

    def track_link(config):
        result = link_facilities(config)
        linked.set()
        return result

    monkeypatch.setattr("app.realtime.refresh_realtime_once", fake_refresh)
    monkeypatch.setattr("app.realtime.link_facilities", track_link)
    refresher = RealtimeRefresher(settings)
    refresher.start()
    try:
        assert linked.wait(timeout=3)
        with connect(settings) as connection:
            assert (
                connection.execute(
                    "SELECT source_id FROM realtime_links WHERE parking_id = 'A'"
                ).fetchone()[0]
                == "RT-A"
            )
        health = realtime_health(
            SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
        )
        assert health["source_statuses"] == 1
        assert health["source_statuses_with_facility"] == 1
        assert health["fresh_linked_lots"] == 1
    finally:
        refresher.stop()


def test_server_refreshes_live_data_on_startup(tmp_path, monkeypatch):
    settings = Settings(
        database_path=str(tmp_path / "server.db"),
        status_service_key="test-key",
        realtime_refresh_seconds=120,
    )
    init_db(settings)
    with connect(settings) as connection:
        _save_page(
            connection,
            "catalog",
            [
                {
                    "prkplceNo": "A",
                    "prkplceNm": "시청 주차장",
                    "latitude": "37.5",
                    "longitude": "127.0",
                }
            ],
        )
        _save_page(
            connection,
            "facility",
            [
                {
                    "prk_center_id": "RT-A",
                    "prk_plce_nm": "시청 주차장",
                    "prk_plce_entrc_la": "37.5",
                    "prk_plce_entrc_lo": "127.0",
                }
            ],
        )
    refreshed = threading.Event()
    linked = threading.Event()

    def track_link(config):
        result = link_facilities(config)
        linked.set()
        return result

    def fake_refresh(config, stop):
        with connect(config) as connection:
            _save_page(connection, "realtime", [_status(8)])
        refreshed.set()
        return 1

    monkeypatch.setattr("app.realtime.refresh_realtime_once", fake_refresh)
    monkeypatch.setattr("app.realtime.link_facilities", track_link)
    with TestClient(create_app(parking_settings=settings)) as client:
        assert refreshed.wait(timeout=3)
        assert linked.wait(timeout=3)
        congestion = client.get("/parking-lots/A/congestion").json()
        assert congestion["available_spaces"] == 8
        assert congestion["level"] == "available"
        assert congestion["fetched_at"] is not None
        health = client.get("/health/realtime").json()
        assert health["fresh_linked_lots"] == 1
        assert health["total_lots"] == 1
        assert health["linked_lots"] == 1
        assert health["source_statuses"] == 1
        assert health["source_statuses_with_facility"] == 1
        assert health["last_fetched_at"] == congestion["fetched_at"]
