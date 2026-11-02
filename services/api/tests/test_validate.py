from datetime import UTC, datetime, timedelta

from app.enums import RejectReason
from app.schemas.gbfs import StationStatusRow
from app.services.validation import validate_station_status

NOW = datetime(2026, 11, 1, 22, 0, tzinfo=UTC)
TOLERANCE_S = 300
BASE = StationStatusRow(
    system_id="x",
    station_id="s1",
    reported_at=NOW - timedelta(seconds=60),
    bikes_available=5,
    ebikes_available=2,
    bikes_disabled=1,
    docks_available=10,
    docks_disabled=0,
    is_installed=True,
    is_renting=True,
    is_returning=True,
)


def check(row: StationStatusRow, capacity: dict[str, int] | None = None) -> str | None:
    return validate_station_status([row], capacity, NOW, TOLERANCE_S)[0]


def test_valid_row_passes():
    assert check(BASE, {"s1": 20}) is None


def test_negative_counter():
    assert check(BASE.model_copy(update={"docks_available": -1})) == RejectReason.NEGATIVE_COUNTER


def test_exceeds_capacity():
    row = BASE.model_copy(update={"bikes_available": 15, "bikes_disabled": 10})
    assert check(row, {"s1": 20}) == RejectReason.EXCEEDS_CAPACITY
    assert check(row, {"s1": 30}) is None
    assert check(row, None) is None


def test_unknown_capacity_is_ignored():
    assert check(BASE, {"other": 5}) is None


def test_reported_in_future_with_small_tolerance():
    far = BASE.model_copy(update={"reported_at": NOW + timedelta(hours=1)})
    near = BASE.model_copy(update={"reported_at": NOW + timedelta(seconds=60)})
    assert check(far) == RejectReason.REPORTED_IN_FUTURE
    assert check(near) is None


def test_none_reported_at_is_valid():
    assert check(BASE.model_copy(update={"reported_at": None})) is None


def test_missing_required_field_and_duplicates():
    assert check(BASE.model_copy(update={"is_renting": None})) == "missing_field:is_renting"
    assert validate_station_status([BASE, BASE], None, NOW, TOLERANCE_S) == [
        None,
        RejectReason.DUPLICATE_STATION,
    ]


def test_ebikes_cannot_exceed_bikes():
    row = BASE.model_copy(update={"ebikes_available": 9})
    assert check(row) == RejectReason.EBIKES_EXCEED_BIKES
