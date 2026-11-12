import httpx
import pytest
import traceback
import app.sources as sources

from app.config import Settings
from app.sources import SourceError, fetch_page


def test_realtime_source_contract():
    def handle(request):
        assert request.url.params["format"] == "2"
        assert request.url.params["serviceKey"] == "test-key"
        return httpx.Response(200, json={
            "resultCode": "0", "totalCount": "1",
            "PrkRealtimeInfo": [{
                "prk_center_id": "A", "pkfc_ParkingLots_total": "100",
                "pkfc_Available_ParkingLots_total": "50",
            }],
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        items, total = fetch_page(client, Settings(
            catalog_service_key="catalog-key", status_service_key="test-key"
        ), "realtime", 1, 100)
    assert total == 1
    assert items[0]["pkfc_Available_ParkingLots_total"] == "50"


def test_catalog_error_is_not_treated_as_empty_page():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"response": {"header": {"resultCode": "22"}}})
    )) as client:
        try:
            fetch_page(client, Settings(catalog_service_key="test-key"), "catalog", 1, 100)
        except SourceError:
            pass
        else:
            raise AssertionError("quota error should be reported")


def test_catalog_top_level_header_body_response():
    payload = {
        "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
        "body": {"totalCount": 1, "items": {"item": [{"prkplceNo": "A"}]}},
    }
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as client:
        items, total = fetch_page(client, Settings(catalog_service_key="test-key"), "catalog", 1, 100)
    assert total == 1
    assert items == [{"prkplceNo": "A"}]


def test_each_provider_uses_its_own_key():
    seen = []

    def handle(request):
        seen.append((request.url.path, request.url.params["serviceKey"], request.url.params))
        if "tn_pubr" in request.url.path:
            return httpx.Response(200, json={
                "response": {"header": {"resultCode": "00"}, "body": {"items": [], "totalCount": 0}}
            })
        field = "PrkRealtimeInfo" if "Realtime" in request.url.path else "PrkSttusInfo"
        return httpx.Response(200, json={"resultCode": "0", "totalCount": 0, field: []})

    settings = Settings(catalog_service_key="first-key", status_service_key="second-key")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        for kind in ("catalog", "facility", "realtime"):
            fetch_page(client, settings, kind, 1, 10, sido_code="11", sigungu_code="11110")
    assert seen[0][1] == "first-key"
    assert [key for _, key, _ in seen[1:]] == ["second-key", "second-key"]
    assert "sidoCd" not in seen[0][2]
    assert all(params["sidoCd"] == "11" and params["sigunguCd"] == "11110" for _, _, params in seen[1:])


def test_placeholder_is_rejected_before_request():
    def unexpected_request(request):
        raise AssertionError("a placeholder must never be sent")

    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as client:
        with pytest.raises(SourceError, match="PARKING_CATALOG_SERVICE_KEY"):
            fetch_page(client, Settings(catalog_service_key="첫번째_API의_디코딩_인증키"), "catalog", 1, 10)


def test_http_error_traceback_does_not_expose_key():
    secret = "sensitive-test-key"
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(403)
    )) as client:
        with pytest.raises(SourceError) as raised:
            fetch_page(client, Settings(catalog_service_key=secret), "catalog", 1, 10)
    assert "HTTP 403" in str(raised.value)
    assert secret not in "".join(traceback.format_exception(raised.value))


def test_transient_http_error_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(sources.time, "sleep", lambda seconds: None)

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"resultCode": "0", "totalCount": 0, "PrkSttusInfo": []})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        items, total = fetch_page(client, Settings(status_service_key="test-key"), "facility", 16, 1000)
    assert (items, total) == ([], 0)
    assert len(calls) == 2

