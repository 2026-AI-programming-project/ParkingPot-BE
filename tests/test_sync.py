import importlib

from app.config import Settings
from app.db import connect


def test_sync_resumes_after_page_limit(tmp_path, monkeypatch):
    module = importlib.import_module("app.sync")
    settings = Settings(database_path=str(tmp_path / "sync.db"))
    pages = []

    def fake_fetch(client, config, kind, page, page_size, **kwargs):
        pages.append(page)
        return ([{
            "prk_center_id": f"P{page}",
            "pkfc_ParkingLots_total": "10",
            "pkfc_Available_ParkingLots_total": "5",
        }], 3)

    monkeypatch.setattr(module, "fetch_page", fake_fetch)
    assert module.sync(settings, ["realtime"], page_size=1, max_pages=1)["realtime"] == 1
    assert module.sync(settings, ["realtime"], page_size=1, max_pages=2)["realtime"] == 2
    assert pages == [1, 2, 3]
    with connect(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM realtime_status").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM sync_progress").fetchone()[0] == 0


def test_facility_link_requires_unique_same_name_within_250m(tmp_path):
    module = importlib.import_module("app.sync")
    settings = Settings(database_path=str(tmp_path / "links.db"))
    module.init_db(settings)
    with connect(settings) as connection:
        module._save_page(connection, "catalog", [{
            "prkplceNo": "A", "prkplceNm": "시청 주차장",
            "latitude": "37.5", "longitude": "127.0",
        }])
        module._save_page(connection, "facility", [{
            "prk_center_id": "RT-A", "prk_plce_nm": "시청 주차장",
            "prk_plce_entrc_la": "37.502", "prk_plce_entrc_lo": "127.0",
        }])
    assert module.link_facilities(settings) == 1
    with connect(settings) as connection:
        connection.execute("DELETE FROM realtime_links")
        module._save_page(connection, "catalog", [{
            "prkplceNo": "B", "prkplceNm": "시청 주차장",
            "latitude": "37.501", "longitude": "127.0",
        }])
    assert module.link_facilities(settings) == 0
