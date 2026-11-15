from datetime import UTC, datetime

import pytest
from conftest import read_fixture

from app.core.config.settings import get_settings
from app.services.normalization import (
    electric_vehicle_type_ids,
    normalize_regions,
    normalize_station_information,
    normalize_station_status,
    normalize_system_information,
    normalize_vehicle_types,
    parse_discovery,
)

ELECTRIC = get_settings().electric_propulsion_types


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_discovery_v2_and_v3():
    v2 = parse_discovery(read_fixture("citibike_v2_3", "gbfs"))
    assert v2.version == "2.3"
    assert {"station_status", "station_information"} <= set(v2.feeds)

    v3 = parse_discovery(read_fixture("synthetic_v3_0", "gbfs"))
    assert v3.version == "3.0"
    assert v3.feeds["station_status"].endswith("station_status.json")


def test_v2_station_status_placeholder_reported_at_is_none():
    vehicle_types = normalize_vehicle_types(
        read_fixture("citibike_v2_3", "vehicle_types"), "lyft_nyc"
    )
    assert electric_vehicle_type_ids(vehicle_types, ELECTRIC) == {"2"}

    status = normalize_station_status(
        read_fixture("citibike_v2_3", "station_status"), "lyft_nyc", vehicle_types, ELECTRIC
    )
    assert len(status) == 7
    assert all(row.system_id == "lyft_nyc" for row in status)
    assert any(row.reported_at is None for row in status)
    assert any(row.reported_at is not None for row in status)
    assert all(isinstance(row.is_renting, bool) for row in status)


def test_v3_fields_rfc3339_and_localized_strings():
    vehicle_types = normalize_vehicle_types(
        read_fixture("synthetic_v3_0", "vehicle_types"), "synthetic_v3"
    )
    assert [v.name for v in vehicle_types] == ["Classic", "E-bike"]

    status = normalize_station_status(
        read_fixture("synthetic_v3_0", "station_status"), "synthetic_v3", vehicle_types, ELECTRIC
    )
    assert [row.bikes_available for row in status] == [8, 0, 5]
    assert [row.ebikes_available for row in status] == [3, 0, 1]
    assert [row.bikes_disabled for row in status] == [1, 0, 0]
    assert status[0].reported_at == utc("2026-11-01 21:43:30")
    # дробные секунды сохраняются, смещение +01:00 учитывается
    assert status[1].reported_at == utc("2026-11-01 21:43:00.250")
    assert status[2].reported_at == utc("2026-11-01 21:43:00")


def test_v3_reference_feeds():
    info = normalize_system_information(read_fixture("synthetic_v3_0", "system_information"), "x")
    assert (info.name, info.timezone) == ("Synthetic Bikes", "Europe/Paris")

    stations = normalize_station_information(
        read_fixture("synthetic_v3_0", "station_information"), "x"
    )
    assert [s.name for s in stations] == ["Gare", "Louvre", "Bastille"]
    assert [s.short_name for s in stations] == ["G1", None, None]
    assert [s.capacity for s in stations] == [20, 10, 12]

    regions = normalize_regions(read_fixture("synthetic_v3_0", "system_regions"), "x")
    assert [r.name for r in regions] == ["Centre"]


def test_without_vehicle_types_uses_num_ebikes_available():
    status = normalize_station_status(
        read_fixture("citibike_v2_3", "station_status"), "lyft_nyc", None, ELECTRIC
    )
    assert any((row.ebikes_available or 0) > 0 for row in status)


def test_incomplete_station_reference_is_rejected():
    feed = {
        "data": {
            "stations": [
                {"station_id": "a", "name": "A", "lat": 1, "lon": 2},
                {"station_id": "b", "name": "B", "lat": 1},
                "мусор",
            ]
        }
    }
    with pytest.raises(ValueError, match="некорректный список"):
        normalize_station_information(feed, "x")


@pytest.mark.parametrize("value", [1.5, float("inf"), float("nan"), "not a number"])
def test_invalid_required_counter_reaches_validation(value):
    from app.services.validation import validate_station_status

    feed = {
        "data": {
            "stations": [
                {
                    "station_id": "s1",
                    "num_bikes_available": value,
                    "is_installed": True,
                    "is_renting": True,
                    "is_returning": True,
                }
            ]
        }
    }
    rows = normalize_station_status(feed, "x", None, ELECTRIC)
    assert len(rows) == 1
    assert validate_station_status(rows, None, datetime.now(UTC), 300) == [
        "missing_field:bikes_available"
    ]


@pytest.mark.parametrize("value", [-1, 2, float("nan")])
def test_invalid_flag_is_not_converted_to_true(value):
    from app.schemas.types import to_flag

    assert to_flag(value) is None


def test_naive_timestamp_has_consistent_utc_meaning(monkeypatch):
    import os
    import time

    from app.schemas.types import to_utc

    old_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        assert to_utc("2026-11-01T12:00:00") == datetime(2026, 11, 1, 12, tzinfo=UTC)
    finally:
        if old_tz is None:
            monkeypatch.delenv("TZ")
        else:
            monkeypatch.setenv("TZ", old_tz)
        time.tzset()
