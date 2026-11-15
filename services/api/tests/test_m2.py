import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import psycopg
import pytest
from conftest import read_fixture
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError

from app.api.auth import current_user
from app.core.config.settings import Settings, get_settings
from app.core.database import Database
from app.main import create_app
from app.models import StationCurrent, StationEvent, StationRollup, StationStatus
from app.repositories.notifications import snapshot_ready
from app.repositories.station_state import StationStateRepository
from app.schemas.gbfs import StationStatusRow
from app.services.access import Access
from app.services.auth import Principal
from app.services.dashboard import ReadCapacityError, ReadService
from app.services.ingestion import IngestService
from app.services.stream import StreamService

ADMIN_ACCESS = Access("admin", None, None)


def row(now, bikes=5, **kwargs):
    return StationStatusRow(
        system_id="synthetic_v3",
        station_id="s1",
        reported_at=now,
        bikes_available=bikes,
        docks_available=10 - bikes,
        is_installed=True,
        is_renting=True,
        is_returning=True,
        **kwargs,
    )


def write(database, rows, now):
    with database.transaction() as session:
        return StationStateRepository(session).write_status(rows, "synthetic_v3", now)


def seed(database):
    feeds = {
        name: read_fixture("synthetic_v3_0", name)
        for name in ("system_information", "station_information")
    }
    with database.transaction() as session:
        IngestService(session, get_settings()).apply_reference("synthetic_v3", "3.0", feeds)
    now = datetime.now(UTC)
    write(database, [row(now)], now)
    return now


def test_events_and_observation_intervals(ingest_db):
    now = datetime.now(UTC)
    assert write(ingest_db, [row(now)], now) == 1
    assert write(ingest_db, [row(now + timedelta(seconds=30))], now + timedelta(seconds=30)) == 0
    with ingest_db.session() as session:
        fact = session.scalar(select(StationStatus))
        assert fact.observed_until == now + timedelta(seconds=30)
        assert session.scalar(select(func.count()).select_from(StationEvent)) == 0
    assert write(ingest_db, [row(now + timedelta(seconds=60), 0)], now + timedelta(seconds=60)) == 1
    with ingest_db.session() as session:
        events = list(session.scalars(select(StationEvent).order_by(StationEvent.id)))
        assert {event.event_type for event in events} == {"bikes_removed", "empty"}
        assert events[0].details == {"count": 5, "lower_bound": True}
    # После перерыва нельзя объявить прирост счётчика арендой/возвратом.
    later = now + timedelta(hours=1)
    write(ingest_db, [row(later, 0)], later)
    with ingest_db.session() as session:
        assert session.scalar(select(func.count()).select_from(StationStatus)) == 3
        assert session.scalar(select(func.count()).select_from(StationEvent)) == 2


def test_notifications_are_committed_with_facts(ingest_db):
    cfg = get_settings()
    with psycopg.connect(
        host=cfg.pg_host,
        port=cfg.pg_port,
        dbname=cfg.pg_database,
        user=cfg.pg_user,
        password=cfg.pg_password.get_secret_value(),
        autocommit=True,
    ) as listener:
        listener.execute("listen snapshot_ready")
        with pytest.raises(RuntimeError), ingest_db.transaction() as session:
            snapshot_ready(session, "rolled_back")
            raise RuntimeError("rollback")
        assert list(listener.notifies(timeout=0.1, stop_after=1)) == []
        with ingest_db.transaction() as session:
            snapshot_ready(session, "committed")
        messages = list(listener.notifies(timeout=2, stop_after=1))
        assert len(messages) == 1 and "committed" in messages[0].payload


def test_api_reads_quality_pagination_and_history(ingest_db, monkeypatch):
    now = seed(ingest_db)
    monkeypatch.setenv("DB_ROLE", "app_api")
    get_settings.cache_clear()
    Database.reset_instance()
    app = create_app()
    app.dependency_overrides[current_user] = lambda: Principal(
        sub="1",
        role="admin",
        system_id=None,
        regions=None,
        iat=0,
        exp=9999999999,
        iss="gbfs-api",
        aud="gbfs-dashboard",
    )
    with TestClient(app) as client:
        systems = client.get("/api/v1/systems")
        assert systems.status_code == 200 and systems.json()[0]["id"] == "synthetic_v3"
        prefix = "/api/v1/systems/synthetic_v3"
        stations = client.get(prefix + "/stations?limit=1").json()
        assert stations["stations"][0]["quality"] == "fresh"
        assert stations["next_after"] == "s1"
        assert (
            client.get(prefix + "/stations?after=s1").json()["stations"][0]["quality"] == "unknown"
        )
        assert client.get(prefix + "/stations/s1").status_code == 200
        assert client.get(prefix + "/stations/missing").status_code == 404
        assert client.get("/api/v1/systems/missing/stations").status_code == 404
        params = {
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
        }
        assert len(client.get(prefix + "/stations/s1/history", params=params).json()["items"]) == 1
        assert client.get(prefix + "/stations/s1/rollups", params=params).json()["items"] == []
        params["start"] = "2026-01-01T00:00:00"
        assert client.get(prefix + "/stations/s1/history", params=params).status_code == 422


def test_read_cache_coalesces_and_invalidation_drops_inflight_result():
    async def run():
        settings = Settings(_env_file=None, read_cache_size=2)
        service = ReadService(Mock(), settings)
        calls = []

        def load():
            calls.append(True)
            return {"value": len(calls)}

        try:
            responses = await asyncio.gather(
                *(service.read(("x", "snapshot"), load) for _ in range(100))
            )
            assert len(calls) == 1 and all(value == responses[0] for value in responses)
            service.invalidate("x")
            await service.read(("x", "snapshot"), load)
            assert len(calls) == 2
            for i in range(3):
                await service.read((str(i), "snapshot"), load)
            assert len(service.cache) == 2
        finally:
            await service.close()

    asyncio.run(run())


def test_sse_queue_limit_and_client_cleanup():
    settings = Settings(_env_file=None, sse_max_clients=1, sse_queue_size=1)
    hub = StreamService(Mock(), settings)
    queue = hub.subscribe("x", ADMIN_ACCESS)
    with pytest.raises(ReadCapacityError):
        hub.subscribe("x", ADMIN_ACCESS)
    hub.broadcast(("x", ADMIN_ACCESS), "delta", {"a": 1})
    hub.broadcast(("x", ADMIN_ACCESS), "delta", {"a": 2})
    assert "event: reset" in queue.get_nowait()
    hub.unsubscribe("x", queue, ADMIN_ACCESS)
    assert hub.count == 0 and not hub.clients


def test_analytics_role_cannot_write_core_and_ingest_cannot_write_mart(ingest_db):
    with pytest.raises(ProgrammingError), ingest_db.transaction() as session:
        session.add(
            StationRollup(
                system_id="x", station_id="x", resolution="day", bucket_start=datetime.now(UTC)
            )
        )
    settings = get_settings().model_copy(update={"db_role": "app_analytics"})
    database = Database(settings)
    try:
        with database.session() as session:
            assert session.scalar(select(func.count()).select_from(StationStatus)) == 0
        with pytest.raises(ProgrammingError), database.transaction() as session:
            session.add(StationCurrent(system_id="x", station_id="x"))
    finally:
        database.dispose()


def test_listen_delivers_committed_delta(ingest_db):
    now = seed(ingest_db)

    async def run():
        reads = ReadService(ingest_db, get_settings())
        hub = StreamService(reads, get_settings())
        queue = hub.subscribe("synthetic_v3", ADMIN_ACCESS)
        hub.start()
        try:
            async with asyncio.timeout(5):
                while not hub.connected:
                    await asyncio.sleep(0.01)
            await hub.publish(("synthetic_v3", ADMIN_ACCESS))
            await queue.get()
            later = now + timedelta(seconds=1)
            await asyncio.to_thread(write, ingest_db, [row(later, 0)], later)
            message = await asyncio.wait_for(queue.get(), 5)
            assert '"bikes_available":0' in message and "event: delta" in message
        finally:
            hub.unsubscribe("synthetic_v3", queue, ADMIN_ACCESS)
            await hub.close()
            await reads.close()

    asyncio.run(run())


def test_stale_and_invalid_data_are_not_presented_as_fresh(ingest_db):
    from app.repositories.dashboard import DashboardRepository

    now = seed(ingest_db)
    old = now - timedelta(hours=1)
    with ingest_db.transaction() as session:
        current = session.get(StationCurrent, ("synthetic_v3", "s1"))
        current.reported_at = old
    with ingest_db.session() as session:
        station = DashboardRepository(session).snapshot("synthetic_v3", 300)["stations"][0]
        assert station["quality"] == "stale" and station["status"]["bikes_available"] == 5
    with ingest_db.transaction() as session:
        StationStateRepository(session).quarantine(
            [(row(now), "bad_sample")], now + timedelta(seconds=1)
        )
    with ingest_db.session() as session:
        assert (
            DashboardRepository(session).snapshot("synthetic_v3", 300)["stations"][0]["quality"]
            == "invalid"
        )


def test_regressing_source_timestamp_does_not_shorten_confirmed_history(ingest_db):
    now = datetime.now(UTC)
    write(ingest_db, [row(now)], now)
    write(ingest_db, [row(now - timedelta(seconds=60))], now + timedelta(seconds=30))
    with ingest_db.session() as session:
        assert session.scalar(select(StationStatus.observed_until)) == now


def test_m2_upgrade_preserves_m1_observations(pg_database):
    from alembic import command
    from alembic.config import Config
    from conftest import API_DIR
    from sqlalchemy import text

    config = Config(str(API_DIR / "alembic.ini"))
    command.downgrade(config, "0001")
    database = Database(get_settings())
    now = datetime.now(UTC)
    reported = now - timedelta(seconds=30)
    try:
        with database.transaction() as session:
            session.execute(
                text(
                    "insert into core.station_status "
                    "(system_id, station_id, fetched_at, reported_at, bikes_available, "
                    "is_installed, is_renting, is_returning) "
                    "values ('legacy', 's1', :now, :reported, 4, true, true, true)"
                ),
                {"now": now, "reported": reported},
            )
        command.upgrade(config, "head")
        with database.session() as session:
            fact = session.scalar(select(StationStatus).where(StationStatus.system_id == "legacy"))
            assert fact.bikes_available == 4 and fact.observed_until == reported
        command.check(config)
    finally:
        database.dispose()
