"""Валидация снимков station_status (ADR-0002, п. 5).

Для каждой строки возвращается причина отбраковки или None. Отбракованные строки уходят
в core.station_quarantine и не попадают в факты и агрегаты.
"""

from datetime import datetime, timedelta

from app.enums import RejectReason
from app.schemas.gbfs import StationStatusRow

REQUIRED_STATUS_COLUMNS = ("bikes_available", "is_installed", "is_renting", "is_returning")
COUNTER_COLUMNS = (
    "bikes_available",
    "ebikes_available",
    "bikes_disabled",
    "docks_available",
    "docks_disabled",
)


def missing_field(name: str) -> str:
    return f"missing_field:{name}"


def _check_row(
    row: StationStatusRow,
    capacity: int | None,
    now: datetime,
    future_tolerance: timedelta,
    duplicate: bool,
) -> str | None:
    if not row.station_id:
        return RejectReason.MISSING_STATION_ID
    for name in REQUIRED_STATUS_COLUMNS:
        if getattr(row, name) is None:
            return missing_field(name)
    if duplicate:
        return RejectReason.DUPLICATE_STATION
    if any((value := getattr(row, name)) is not None and value < 0 for name in COUNTER_COLUMNS):
        return RejectReason.NEGATIVE_COUNTER
    if capacity is not None and capacity > 0:
        bikes = row.bikes_available + (row.bikes_disabled or 0)
        docks = (row.docks_available or 0) + (row.docks_disabled or 0)
        if bikes > capacity or docks > capacity:
            return RejectReason.EXCEEDS_CAPACITY
    if row.ebikes_available is not None and row.ebikes_available > row.bikes_available:
        return RejectReason.EBIKES_EXCEED_BIKES
    if row.reported_at is not None and row.reported_at > now + future_tolerance:
        return RejectReason.REPORTED_IN_FUTURE
    return None


def validate_station_status(
    rows: list[StationStatusRow],
    capacity: dict[str, int] | None,
    now: datetime,
    future_tolerance_s: int,
) -> list[str | None]:
    tolerance = timedelta(seconds=future_tolerance_s)
    seen: set[str | None] = set()
    reasons = []
    for row in rows:
        duplicate = row.station_id in seen
        seen.add(row.station_id)
        station_capacity = (capacity or {}).get(row.station_id) if row.station_id else None
        reasons.append(_check_row(row, station_capacity, now, tolerance, duplicate))
    return reasons
