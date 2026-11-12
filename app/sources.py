"""Public data API adapters. Provider field names stay inside this module."""

import httpx
import time

from app.config import Settings


class SourceError(RuntimeError):
    pass


def _items(payload: dict, field: str) -> tuple[list[dict], int]:
    response = payload.get("response", payload)
    if "header" in response or "body" in response:
        header = response.get("header")
        body = response.get("body")
        if not isinstance(header, dict) or not isinstance(body, dict):
            raise SourceError("공공데이터 API 응답에 header 또는 body가 없습니다")
        code = str(header.get("resultCode", "00"))
        raw = body.get("items", [])
        total = body.get("totalCount", 0)
    else:
        code = str(response.get("resultCode", "0"))
        raw = response.get(field, [])
        total = response.get("totalCount", 0)
    if code not in {"0", "00"}:
        raise SourceError(f"공공데이터 API 오류: {code}")
    if isinstance(raw, dict):
        raw = raw.get("item", raw.get("Prk", raw))
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise SourceError("공공데이터 API 응답 형식이 예상과 다릅니다")
    return raw, int(total)


def fetch_page(
    client: httpx.Client,
    settings: Settings,
    kind: str,
    page: int,
    page_size: int,
    sido_code: str | None = None,
    sigungu_code: str | None = None,
) -> tuple[list[dict], int]:
    urls = {
        "catalog": (settings.catalog_url, "items", "type", "json", settings.catalog_service_key, "PARKING_CATALOG_SERVICE_KEY"),
        "facility": (settings.facility_url, "PrkSttusInfo", "format", 2, settings.status_service_key, "PARKING_STATUS_SERVICE_KEY"),
        "realtime": (settings.realtime_url, "PrkRealtimeInfo", "format", 2, settings.status_service_key, "PARKING_STATUS_SERVICE_KEY"),
    }
    url, field, format_key, format_value, service_key, key_name = urls[kind]
    service_key = service_key.strip()
    if not service_key or service_key in {
        "첫번째_API의_디코딩_인증키",
        "두번째_API의_디코딩_인증키",
    }:
        raise SourceError(f".env의 {key_name}에 발급받은 디코딩 인증키를 입력하세요")
    params = {
        "serviceKey": service_key,
        "pageNo": page,
        "numOfRows": page_size,
        format_key: format_value,
    }
    if kind != "catalog":
        if sido_code:
            params["sidoCd"] = sido_code
        if sigungu_code:
            params["sigunguCd"] = sigungu_code
    for attempt in range(4):
        try:
            response = client.get(
                url,
                params=params,
                timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            )
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return _items(response.json(), field)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            hint = " — 인증키와 해당 API의 활용신청 상태를 확인하세요" if code in {401, 403} else ""
            raise SourceError(f"공공데이터 API HTTP {code} ({kind}, {page}페이지){hint}") from None
        except httpx.TimeoutException:
            if attempt < 3:
                time.sleep(2**attempt)
                continue
            raise SourceError(f"공공데이터 API 시간 초과 ({kind}, {page}페이지, 4회 시도)") from None
        except httpx.TransportError:
            if attempt < 3:
                time.sleep(2**attempt)
                continue
            raise SourceError(f"공공데이터 API 연결 오류 ({kind}, {page}페이지, 4회 시도)") from None
        except (ValueError, TypeError, KeyError):
            raise SourceError(f"공공데이터 API 응답 해석 실패 ({kind}, {page}페이지)") from None
    raise AssertionError("unreachable")

