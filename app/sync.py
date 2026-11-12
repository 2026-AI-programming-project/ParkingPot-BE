"""Import public data into SQLite. Run with ``python -m app.sync``."""

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from math import ceil

import httpx

from app.config import Settings
from app.db import connect, init_db
from app.geo import distance_m
from app.sources import SourceError, fetch_page


def _number(value) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _coordinate(value, minimum: float, maximum: float) -> float | None:
    try:
        number = float(value)
        return number if minimum <= number <= maximum else None
    except (TypeError, ValueError):
        return None


def _fee_type(value: str) -> str:
    if "유료" in value and "무료" in value:
        return "mixed"
    if "무료" in value:
        return "free"
    if "유료" in value:
        return "paid"
    return "unknown"


def _hourly_price(item: dict, fee_type: str) -> int | None:
    if fee_type == "free":
        return 0
    if fee_type != "paid":
        return None
    base_minutes = _number(item.get("basicTime"))
    base_price = _number(item.get("basicCharge"))
    if base_minutes is None or base_price is None or base_minutes <= 0 or base_price < 0:
        return None
    if base_minutes >= 60:
        return base_price if base_minutes == 60 else None
    unit_minutes = _number(item.get("addUnitTime"))
    unit_price = _number(item.get("addUnitCharge"))
    if unit_minutes is None or unit_price is None or unit_minutes <= 0 or unit_price < 0:
        return None
    return base_price + ceil((60 - base_minutes) / unit_minutes) * unit_price


def _catalog_row(item: dict) -> tuple | None:
    parking_id = str(item.get("prkplceNo") or "").strip()
    name = str(item.get("prkplceNm") or "").strip()
    if not parking_id or not name:
        return None
    fee_description = str(item.get("parkingchrgeInfo") or "").strip()
    fee_type = _fee_type(fee_description)
    accessible = str(item.get("pwdbsPpkZoneYn") or "").upper()
    address = str(item.get("rdnmadr") or "").strip() or str(item.get("lnmadr") or "").strip()
    return (
        parking_id,
        name,
        address,
        _coordinate(item.get("latitude"), -90, 90),
        _coordinate(item.get("longitude"), -180, 180),
        fee_type,
        fee_description,
        _hourly_price(item, fee_type),
        1 if accessible == "Y" else 0 if accessible == "N" else None,
        _number(item.get("prkcmprt")),
        item.get("weekdayOperOpenHhmm"),
        item.get("weekdayOperColseHhmm"),
        item.get("satOperOperOpenHhmm"),
        item.get("satOperCloseHhmm"),
        item.get("holidayOperOpenHhmm"),
        item.get("holidayCloseOpenHhmm"),
        item.get("operDay"),
        item.get("referenceDate"),
    )


def _save_page(connection, kind: str, items: list[dict]) -> int:
    count = 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    for item in items:
        if kind == "catalog":
            row = _catalog_row(item)
            if row is None:
                continue
            connection.execute(
                """INSERT INTO parking_lots (
                    id, name, address, latitude, longitude, fee_type, fee_description,
                    hourly_price, accessible, total_spaces, weekday_open, weekday_close,
                    saturday_open, saturday_close, holiday_open, holiday_close,
                    operating_days, reference_date
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, address=excluded.address,
                    latitude=excluded.latitude, longitude=excluded.longitude,
                    fee_type=excluded.fee_type, fee_description=excluded.fee_description,
                    hourly_price=excluded.hourly_price, accessible=excluded.accessible,
                    total_spaces=excluded.total_spaces, weekday_open=excluded.weekday_open,
                    weekday_close=excluded.weekday_close, saturday_open=excluded.saturday_open,
                    saturday_close=excluded.saturday_close, holiday_open=excluded.holiday_open,
                    holiday_close=excluded.holiday_close, operating_days=excluded.operating_days,
                    reference_date=excluded.reference_date""",
                row,
            )
        elif kind == "facility":
            source_id = str(item.get("prk_center_id") or "").strip()
            name = str(item.get("prk_plce_nm") or "").strip()
            if not source_id or not name:
                continue
            connection.execute(
                """INSERT INTO realtime_facilities(source_id, name, latitude, longitude)
                   VALUES (?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                   name=excluded.name, latitude=excluded.latitude, longitude=excluded.longitude""",
                (
                    source_id,
                    name,
                    _coordinate(item.get("prk_plce_entrc_la"), -90, 90),
                    _coordinate(item.get("prk_plce_entrc_lo"), -180, 180),
                ),
            )
        else:
            source_id = str(item.get("prk_center_id") or "").strip()
            total = _number(item.get("pkfc_ParkingLots_total"))
            available = _number(item.get("pkfc_Available_ParkingLots_total"))
            if not source_id or total is None or available is None or total <= 0 or not 0 <= available <= total:
                continue
            connection.execute(
                """INSERT INTO realtime_status(source_id, total_spaces, available_spaces, fetched_at)
                   VALUES (?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                   total_spaces=excluded.total_spaces,
                   available_spaces=excluded.available_spaces,
                   fetched_at=excluded.fetched_at""",
                (source_id, total, available, fetched_at),
            )
        count += 1
    return count


def _normal_name(value: str) -> str:
    return "".join(value.split()).casefold()


def link_facilities(settings: Settings) -> int:
    """Link only unique same-name matches within 250 m, or identical source IDs."""
    with connect(settings) as connection:
        lots = connection.execute("SELECT id, name, latitude, longitude FROM parking_lots").fetchall()
        facilities = connection.execute("SELECT source_id, name, latitude, longitude FROM realtime_facilities")
        by_name = defaultdict(list)
        by_id = {}
        for lot in lots:
            by_name[_normal_name(lot["name"])].append(lot)
            by_id[lot["id"]] = lot
        linked = 0
        for facility in facilities:
            candidates = []
            if facility["source_id"] in by_id:
                candidates = [by_id[facility["source_id"]]]
            elif facility["latitude"] is not None and facility["longitude"] is not None:
                candidates = [
                    lot for lot in by_name[_normal_name(facility["name"])]
                    if lot["latitude"] is not None and lot["longitude"] is not None
                    and distance_m(lot["latitude"], lot["longitude"], facility["latitude"], facility["longitude"]) <= 250
                ]
            if len(candidates) == 1:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO realtime_links(parking_id, source_id) VALUES (?, ?)",
                    (candidates[0]["id"], facility["source_id"]),
                )
                linked += cursor.rowcount
    return linked


def sync(
    settings: Settings,
    kinds: list[str],
    page_size: int = 1000,
    max_pages: int | None = None,
    sido_code: str | None = None,
    sigungu_code: str | None = None,
    restart: bool = False,
) -> dict[str, int]:
    init_db(settings)
    counts = {}
    with httpx.Client() as client:
        for kind in kinds:
            scope = "global" if kind == "catalog" else f"{sido_code or '*'}:{sigungu_code or '*'}"
            with connect(settings) as connection:
                if restart:
                    connection.execute(
                        "DELETE FROM sync_progress WHERE kind = ? AND scope = ? AND page_size = ?",
                        (kind, scope, page_size),
                    )
                cursor = connection.execute(
                    "SELECT next_page FROM sync_progress WHERE kind = ? AND scope = ? AND page_size = ?",
                    (kind, scope, page_size),
                ).fetchone()
                page = cursor["next_page"] if cursor else 1
            imported = 0
            pages_fetched = 0
            while max_pages is None or pages_fetched < max_pages:
                items, total = fetch_page(
                    client, settings, kind, page, page_size,
                    sido_code=sido_code, sigungu_code=sigungu_code,
                )
                finished = not items or page * page_size >= total
                with connect(settings) as connection:
                    imported += _save_page(connection, kind, items)
                    if finished:
                        connection.execute(
                            "DELETE FROM sync_progress WHERE kind = ? AND scope = ? AND page_size = ?",
                            (kind, scope, page_size),
                        )
                    else:
                        connection.execute(
                            """INSERT INTO sync_progress(kind, scope, page_size, next_page, total)
                               VALUES (?, ?, ?, ?, ?) ON CONFLICT(kind, scope, page_size)
                               DO UPDATE SET next_page=excluded.next_page, total=excluded.total""",
                            (kind, scope, page_size, page + 1, total),
                        )
                pages_fetched += 1
                if pages_fetched == 1 or pages_fetched % 10 == 0 or finished:
                    print(f"{kind}: {page}페이지 / 약 {ceil(total / page_size)}페이지, 이번 실행 {imported}건 저장", flush=True)
                if finished:
                    break
                page += 1
            counts[kind] = imported
            if kind == "facility":
                counts["new_links"] = link_facilities(settings)
    with connect(settings) as connection:
        counts["linked_realtime_statuses"] = connection.execute(
            """SELECT COUNT(*) FROM realtime_status s
               JOIN realtime_links l ON l.source_id = s.source_id"""
        ).fetchone()[0]
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="공공데이터 주차장 정보 동기화")
    parser.add_argument("--kind", choices=["all", "catalog", "facility", "realtime"], default="all")
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, help="이번 실행에서 소스별로 가져올 최대 페이지 수")
    parser.add_argument("--sido-code", help="시설·실시간정보 조회 지역 (예: 서울 11)")
    parser.add_argument("--sigungu-code", help="시설·실시간정보 조회 시군구 코드")
    parser.add_argument("--allow-nationwide-facility", action="store_true", help="시간이 오래 걸릴 수 있는 전국 시설정보 조회 허용")
    parser.add_argument("--restart", action="store_true", help="저장된 다음 페이지 위치를 초기화")
    args = parser.parse_args()
    if not 1 <= args.page_size <= 1000 or (args.max_pages is not None and args.max_pages < 1):
        parser.error("page-size는 1~1000, max-pages는 1 이상이어야 합니다")
    if args.sido_code and (len(args.sido_code) != 2 or not args.sido_code.isdigit()):
        parser.error("sido-code는 숫자 2자리여야 합니다")
    if args.sigungu_code and (
        not args.sido_code or len(args.sigungu_code) != 5
        or not args.sigungu_code.isdigit() or not args.sigungu_code.startswith(args.sido_code)
    ):
        parser.error("sigungu-code는 sido-code로 시작하는 숫자 5자리여야 합니다")
    kinds = ["catalog", "facility", "realtime"] if args.kind == "all" else [args.kind]
    if "facility" in kinds and not args.sido_code:
        if not args.allow_nationwide_facility:
            parser.error("전국 시설정보는 약 170만 건이며 깊은 페이지에서 시간 초과가 발생할 수 있습니다. --sido-code와 --sigungu-code를 지정하세요. 전국 조회를 계속하려면 --allow-nationwide-facility를 추가하세요")
        print("전국 시설정보 약 170만 건을 조회합니다. 긴 지연과 시간 초과가 발생할 수 있습니다.", file=sys.stderr)
    try:
        print(sync(
            Settings.from_env(), kinds, args.page_size, args.max_pages,
            args.sido_code, args.sigungu_code, args.restart,
        ))
    except SourceError as exc:
        hint = (
            "전국 시설정보 요청이 지연되고 있습니다. 지역코드를 지정해 다시 실행하세요. 기존 이어받기 위치는 보존됩니다."
            if "facility" in str(exc) and not args.sido_code
            else "같은 명령을 다시 실행하면 실패한 페이지부터 이어집니다."
        )
        parser.exit(1, f"{exc}\n{hint}\n")


if __name__ == "__main__":
    main()

