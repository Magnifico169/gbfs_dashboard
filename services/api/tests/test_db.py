"""Интеграционные тесты на PostgreSQL: схема накатывается настоящим Alembic."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import read_fixture
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError

from app.core.config.settings import get_settings
from app.core.database import Database
from app.models import (
    Region,
    Station,
    StationCurrent,
    StationQuarantine,
    StationStatus,
    SystemSetting,
    VehicleType,
)
from app.repositories.reference import ReferenceRepository
from app.repositories.station_state import StationStateRepository
from app.services.ingestion import IngestService
from app.services.normalization import normalize_station_information

SETTINGS = get_settings()


def count(database: Database, model) -> int:
    with database.session() as session:
        return session.scalar(select(func.count()).select_from(model))


def prepare_partitions(database: Database) -> None:
    with database.transaction() as session:
        StationStateRepository(session).ensure_partitions(SETTINGS.partitions_days_ahead)


def test_v23_reference_facts_and_change_detection(ingest_db):
    prepare_partitions(ingest_db)
    feeds = {
        name: read_fixture("citibike_v2_3", name)
        for name in ("system_information", "station_information", "vehicle_types")
    }
    with ingest_db.transaction() as session:
        reference = IngestService(session, SETTINGS).apply_reference("lyft_nyc", "2.3", feeds)
    assert reference.stations == 7
    assert count(ingest_db, Station) == 7
    assert count(ingest_db, VehicleType) == 2

    def ingest(status: dict):
        with ingest_db.transaction() as session:
            return IngestService(session, SETTINGS).apply_station_status(
                "lyft_nyc", status, reference.vehicle_types
            )

    status = read_fixture("citibike_v2_3", "station_status")
    first = ingest(status)
    assert first.rows_in == 7
    assert first.rows_written + first.rows_quarantined == 7
    assert count(ingest_db, StationStatus) == first.rows_written
    assert count(ingest_db, StationCurrent) == first.rows_written

    assert ingest(status).rows_written == 0
    assert count(ingest_db, StationStatus) == first.rows_written

    status["data"]["stations"][0]["num_bikes_available"] = 99
    moved = ingest(status)
    assert moved.rows_written + moved.rows_quarantined == 1


def test_v3_and_quarantine(ingest_db):
    prepare_partitions(ingest_db)
    feeds = {
        name: read_fixture("synthetic_v3_0", name)
        for name in ("system_information", "station_information", "vehicle_types", "system_regions")
    }
    with ingest_db.transaction() as session:
        reference = IngestService(session, SETTINGS).apply_reference("synthetic_v3", "3.0", feeds)
    assert count(ingest_db, Region) == 1

    status = read_fixture("synthetic_v3_0", "station_status")
    status["data"]["stations"][1]["num_docks_available"] = -3
    status["data"]["stations"][2]["num_vehicles_available"] = 40
    with ingest_db.transaction() as session:
        counts = IngestService(session, SETTINGS).apply_station_status(
            "synthetic_v3", status, reference.vehicle_types
        )
    assert (counts.rows_in, counts.rows_written, counts.rows_quarantined) == (3, 1, 2)

    with ingest_db.session() as session:
        reasons = session.scalars(
            select(StationQuarantine.reason).order_by(StationQuarantine.station_id)
        ).all()
    assert list(reasons) == ["negative_counter", "exceeds_capacity"]


def test_scd2_opens_new_version_on_change(ingest_db):
    stations = normalize_station_information(
        read_fixture("synthetic_v3_0", "station_information"), "synthetic_v3"
    )
    now = datetime.now(UTC)
    with ingest_db.transaction() as session:
        ReferenceRepository(session).sync_stations(
            stations, "synthetic_v3", now - timedelta(hours=1)
        )
    stations[0].capacity = 25
    with ingest_db.transaction() as session:
        closed = ReferenceRepository(session).sync_stations(stations, "synthetic_v3", now)
    assert closed == 1

    with ingest_db.session() as session:
        versions = session.execute(
            select(Station.capacity, Station.valid_to)
            .where(Station.station_id == "s1")
            .order_by(Station.valid_from)
        ).all()
    assert [v.capacity for v in versions] == [20, 25]
    assert [v.valid_to is None for v in versions] == [False, True]
    assert count(ingest_db, Station) == 4


def test_ingest_role_cannot_change_system_settings(ingest_db):
    with pytest.raises(ProgrammingError, match="permission denied"), ingest_db.transaction() as s:
        s.add(SystemSetting(system_id="a", discovery_url="b"))
