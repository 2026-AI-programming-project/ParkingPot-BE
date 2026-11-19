"""Environment based configuration."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_path: str = "parking.db"
    catalog_service_key: str = ""
    status_service_key: str = ""
    catalog_url: str = "https://api.data.go.kr/openapi/tn_pubr_prkplce_info_api"
    facility_url: str = "https://apis.data.go.kr/B553881/Parking_v1/PrkSttusInfo_v1"
    realtime_url: str = "https://apis.data.go.kr/B553881/Parking_v1/PrkRealtimeInfo_v1"
    realtime_refresh_seconds: int = 120
    realtime_sido_code: str | None = None
    realtime_sigungu_code: str | None = None
    realtime_mode: str = "embedded"
    anonymous_idle_days: int = 0

    def __post_init__(self) -> None:
        if self.realtime_mode not in {"embedded", "worker"}:
            raise ValueError("PARKING_REALTIME_MODE must be embedded or worker")
        if self.anonymous_idle_days < 0:
            raise ValueError("PARKING_ANONYMOUS_IDLE_DAYS cannot be negative")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            database_path=os.getenv("PARKING_DB_PATH", "parking.db"),
            catalog_service_key=os.getenv("PARKING_CATALOG_SERVICE_KEY", ""),
            status_service_key=os.getenv("PARKING_STATUS_SERVICE_KEY", ""),
            catalog_url=os.getenv("PARKING_CATALOG_URL", cls.catalog_url),
            facility_url=os.getenv("PARKING_FACILITY_URL", cls.facility_url),
            realtime_url=os.getenv("PARKING_REALTIME_URL", cls.realtime_url),
            realtime_refresh_seconds=int(os.getenv("PARKING_REALTIME_REFRESH_SECONDS", "120")),
            realtime_sido_code=os.getenv("PARKING_REALTIME_SIDO_CODE") or None,
            realtime_sigungu_code=os.getenv("PARKING_REALTIME_SIGUNGU_CODE") or None,
            realtime_mode=os.getenv("PARKING_REALTIME_MODE", cls.realtime_mode),
            anonymous_idle_days=int(
                os.getenv("PARKING_ANONYMOUS_IDLE_DAYS", str(cls.anonymous_idle_days))
            ),
        )
