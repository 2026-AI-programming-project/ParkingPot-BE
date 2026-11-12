"""Refresh live parking availability while the API server is running."""

import logging
import threading

import httpx

from app.config import Settings
from app.db import connect
from app.sources import SourceError, fetch_page
from app.sync import _save_page

logger = logging.getLogger(__name__)


def refresh_realtime_once(settings: Settings, stop: threading.Event | None = None) -> int:
    """Fetch every page before committing, so a failed cycle keeps the last snapshot."""
    items: list[dict] = []
    page = 1
    page_size = 1000
    with httpx.Client() as client:
        while not (stop and stop.is_set()):
            batch, total = fetch_page(
                client,
                settings,
                "realtime",
                page,
                page_size,
                sido_code=settings.realtime_sido_code,
                sigungu_code=settings.realtime_sigungu_code,
            )
            items.extend(batch)
            if not batch or page * page_size >= total:
                break
            page += 1
    if stop and stop.is_set():
        return 0
    with connect(settings) as connection:
        return _save_page(connection, "realtime", items)


class RealtimeRefresher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="parking-realtime-refresh", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                count = refresh_realtime_once(self.settings, self.stop_event)
                if not self.stop_event.is_set():
                    logger.info("실시간 주차정보 %d건 갱신", count)
            except SourceError as exc:
                logger.warning("실시간 주차정보 갱신 실패: %s", exc)
            except Exception:
                logger.exception("실시간 주차정보 갱신 중 예기치 않은 오류")
            self.stop_event.wait(self.settings.realtime_refresh_seconds)
