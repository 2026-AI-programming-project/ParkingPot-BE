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
        )
