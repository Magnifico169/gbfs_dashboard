from datetime import UTC, datetime

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


def test_incomplete_records_are_dropped():
    feed = {
        "data": {
            "stations": [
                {"station_id": "a", "name": "A", "lat": 1, "lon": 2},
                {"station_id": "b", "name": "B", "lat": 1},
                "мусор",
            ]
        }
    }
    assert [s.station_id for s in normalize_station_information(feed, "x")] == ["a"]
