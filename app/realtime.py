"""Refresh live parking availability in the API process or a dedicated worker."""

import errno
import logging
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.config import Settings
from app.parking import REALTIME_STATUS_MAX_AGE
from app.parking_db import connect, init_db
from app.sources import SourceError, fetch_page
from app.sync import _save_page, link_facilities

logger = logging.getLogger(__name__)


@contextmanager
def refresh_lock(database_path: str):
    """Let one refresher own a database on this host without holding a SQLite transaction."""
    database = Path(database_path).resolve()
    lock_path = database.with_name(database.name + ".realtime.lock")
    with lock_path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            yield False
        else:
            # Closing the handle also releases the lock when the process exits.
            yield True


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
        self.fresh_status_ids: set[str] | None = None
        self.thread = threading.Thread(
            target=self._run, name="parking-realtime-refresh", daemon=True
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            with refresh_lock(self.settings.database_path) as acquired:
                if acquired:
                    self._refresh_until_stopped()
                    return
            self.stop_event.wait(self.settings.realtime_refresh_seconds)

    def _refresh_until_stopped(self) -> None:
        while not self.stop_event.is_set():
            try:
                count = refresh_realtime_once(self.settings, self.stop_event)
                if not self.stop_event.is_set():
                    cutoff = (datetime.now(UTC) - REALTIME_STATUS_MAX_AGE).isoformat()
                    with connect(self.settings) as connection:
                        fresh_status_ids = {
                            row[0]
                            for row in connection.execute(
                                "SELECT source_id FROM realtime_status WHERE fetched_at >= ?",
                                (cutoff,),
                            )
                        }
                    # A previously seen source can become fresh again without a new row.
                    if fresh_status_ids != self.fresh_status_ids:
                        updated = link_facilities(self.settings)
                        self.fresh_status_ids = fresh_status_ids
                        if updated:
                            logger.info("실시간 주차정보 연결 %d건 갱신", updated)
                    logger.info("실시간 주차정보 %d건 갱신", count)
            except SourceError as exc:
                logger.warning("실시간 주차정보 갱신 실패: %s", exc)
            except Exception:
                logger.exception("실시간 주차정보 갱신 중 예기치 않은 오류")
            self.stop_event.wait(self.settings.realtime_refresh_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    if not settings.status_service_key or settings.realtime_refresh_seconds <= 0:
        raise SystemExit("Set PARKING_STATUS_SERVICE_KEY and a positive refresh interval.")
    init_db(settings)
    logger.info("Starting ParkingPot realtime worker")
    try:
        RealtimeRefresher(settings)._run()
    except KeyboardInterrupt:
        logger.info("Stopped ParkingPot realtime worker")


if __name__ == "__main__":
    main()
