"""Resolve a user-entered address or place name to map coordinates."""

import httpx
from fastapi import HTTPException

ADDRESS_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KEYWORD_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


def resolve_location(query: str, rest_api_key: str) -> tuple[float, float]:
    """Return (latitude, longitude) for an address, falling back to a place search."""
    if not rest_api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="주소 검색을 사용하려면 .env에 Kakao REST API 키를 설정해야 합니다",
        )

    headers = {"Authorization": f"KakaoAK {rest_api_key.strip()}"}
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(ADDRESS_SEARCH_URL, headers=headers, params={"query": query})
            if response.status_code in {401, 403}:
                raise HTTPException(status_code=502, detail="Kakao 주소 검색 인증에 실패했습니다")
            response.raise_for_status()
            documents = response.json().get("documents", [])
            if documents:
                location = documents[0]
                return float(location["y"]), float(location["x"])

            response = client.get(
                KEYWORD_SEARCH_URL,
                headers=headers,
                params={"query": query, "sort": "accuracy"},
            )
            if response.status_code in {401, 403}:
                raise HTTPException(status_code=502, detail="Kakao 장소 검색 인증에 실패했습니다")
            response.raise_for_status()
            documents = response.json().get("documents", [])
            if documents:
                location = documents[0]
                return float(location["y"]), float(location["x"])
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="주소 검색 서비스 응답 시간이 초과되었습니다") from exc
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail="주소 검색 서비스에 연결할 수 없습니다") from exc

    raise HTTPException(status_code=404, detail="입력한 주소나 장소를 찾을 수 없습니다")
