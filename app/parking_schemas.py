"""HTTP response schemas for the parking API."""

from typing import Literal

from pydantic import BaseModel, Field


class ParkingHours(BaseModel):
    open: str | None
    close: str | None


class ParkingOpeningHours(BaseModel):
    weekday: ParkingHours
    saturday: ParkingHours
    holiday: ParkingHours


class ParkingRating(BaseModel):
    average: float | None = Field(description="등록된 별점이 없으면 null")
    count: int


class ParkingCongestion(BaseModel):
    level: Literal["unknown", "available", "moderate", "busy"]
    color: Literal["gray", "green", "yellow", "red"]
    available_percent: int | None = Field(description="최신 실시간 데이터가 없으면 null")
    available_spaces: int | None
    total_spaces: int | None
    fetched_at: str | None


class ParkingLot(BaseModel):
    id: str
    data_source: Literal["catalog", "realtime_facility"] = "catalog"
    source_id: str | None = None
    name: str
    address: str
    latitude: float | None
    longitude: float | None
    fee_type: Literal["free", "paid", "mixed", "unknown"]
    keywords: list[str]
    fee_description: str
    hourly_price: int | None = Field(description="시간당 요금(원). 알 수 없으면 null")
    accessible: bool | None
    total_spaces: int | None
    operating_days: str | None
    opening_hours: ParkingOpeningHours
    rating: ParkingRating
    reference_date: str | None
    distance_m: int | None = Field(description="검색에 출발 좌표가 없으면 null")
    congestion: ParkingCongestion


class ParkingSearchItem(ParkingLot):
    is_nearest: bool


class ParkingSearchResult(BaseModel):
    items: list[ParkingSearchItem]
    total: int
    page: int
    page_size: int
    nearest_id: str | None = Field(description="필터 결과 전체에서 가장 가까운 주차장 ID")


class FavoriteList(BaseModel):
    items: list[ParkingLot]


class FavoriteResult(BaseModel):
    parking_id: str
    favorite: bool


class AnonymousSession(BaseModel):
    access_token: str
    token_type: Literal["bearer"]


class RealtimeHealth(BaseModel):
    last_fetched_at: str | None
    total_lots: int
    linked_lots: int
    source_statuses: int
    source_statuses_with_facility: int
    fresh_linked_lots: int
    fresh_mappable_facilities: int
    refresh_running: bool
    refresh_interval_seconds: int


class LiveParkingFacility(BaseModel):
    source_id: str
    parking_id: str | None
    name: str
    latitude: float
    longitude: float
    distance_m: int | None
    congestion: ParkingCongestion


class LiveParkingSearchResult(BaseModel):
    items: list[LiveParkingFacility]
    total: int
    page: int
    page_size: int
