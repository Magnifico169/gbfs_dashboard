from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from conftest import read_fixture
from sqlalchemy import func, select

from app.core.config.settings import Settings, get_settings
from app.models import IngestRun, StationCurrent, StationStatus
from app.repositories.station_state import StationStateRepository
from app.schemas.ingest import FetchResult
from app.services.ingestion import IngestService, Poller
from app.services.models import SystemState


class FeedClient:
    def __init__(self):
        self.feeds = {
            name: read_fixture("synthetic_v3_0", name)
            for name in (
                "system_information",
                "station_information",
                "station_status",
                "vehicle_types",
            )
        }
        self.feeds["gbfs"] = {
            "version": "3.0",
            "data": {"feeds": [{"name": name, "url": name} for name in self.feeds]},
        }
        self.etag = "v1"
        self.calls = []

    def fetch_json(self, url, etag=None):
        self.calls.append((url, etag))
        if etag is not None and etag == self.etag:
            return FetchResult(status=304, etag=etag)
        return FetchResult(status=200, etag=self.etag, body=self.feeds[url])


@pytest.fixture
def poller(ingest_db):
    with ingest_db.transaction() as session:
        StationStateRepository(session).ensure_partitions(2)
    return Poller(ingest_db, FeedClient(), get_settings())


def state():
    return SystemState(system_id="synthetic_v3", discovery_url="gbfs")


def test_reference_304_reuses_committed_bodies(poller):
    system = state()
    now = datetime.now(UTC)
    poller.refresh_reference(system, now)
    vehicle_types = system.vehicle_types
    poller.refresh_reference(system, now + timedelta(hours=2))
    assert system.reference_at == now + timedelta(hours=2)
    assert system.vehicle_types == vehicle_types
    assert all(etag == "v1" for _, etag in poller.client.calls[4:])
    with poller.database.session() as session:
        assert session.scalar(select(func.count()).select_from(IngestRun)) == 8


def test_reference_failure_does_not_advance_etags(poller, monkeypatch):
    system = state()
    now = datetime.now(UTC)
    with monkeypatch.context() as patch:
        patch.setattr(IngestService, "apply_reference", Mock(side_effect=RuntimeError("DB failed")))
        with pytest.raises(RuntimeError, match="DB failed"):
            poller.refresh_reference(system, now)
    assert not system.etags and not system.bodies and not system.feed_urls
    poller.refresh_reference(system, now)
    assert system.etags["gbfs"] == "v1"


def test_status_rollback_retry_and_304_freshness(poller, monkeypatch):
    system = state()
    now = datetime.now(UTC)
    poller.ingest_system(system, now)
    with poller.database.session() as session:
        first = session.scalar(select(StationCurrent).where(StationCurrent.station_id == "s1"))
        fetched_at, checked_at = first.fetched_at, first.checked_at
    poller.ingest_system(system, now + timedelta(minutes=1))
    with poller.database.session() as session:
        current = session.scalar(select(StationCurrent).where(StationCurrent.station_id == "s1"))
        assert current.fetched_at == fetched_at
        assert current.checked_at > checked_at
        assert session.scalar(select(func.count()).select_from(StationStatus)) == 3
    poller.client.etag = "v2"
    poller.client.feeds["station_status"]["data"]["stations"][0]["num_vehicles_available"] = 9
    with monkeypatch.context() as patch:
        patch.setattr(
            IngestService, "apply_station_status", Mock(side_effect=RuntimeError("write failed"))
        )
        with pytest.raises(RuntimeError, match="write failed"):
            poller.ingest_system(system, now + timedelta(minutes=2))
    assert system.etags["station_status"] == "v1"
    poller.ingest_system(system, now + timedelta(minutes=3))
    assert system.etags["station_status"] == "v2"
    with poller.database.session() as session:
        assert session.scalar(select(func.count()).select_from(StationStatus)) == 4
        assert session.scalar(select(IngestRun.status).order_by(IngestRun.id.desc())) == "ok"


def test_200_without_etag_clears_previous_validator(poller):
    system = state()
    now = datetime.now(UTC)
    poller.ingest_system(system, now)
    poller.client.etag = None
    poller.ingest_system(system, now)
    assert "station_status" not in system.etags


def test_changed_discovery_url_resets_state(poller, monkeypatch):
    system = SystemState(system_id="lyft_nyc", discovery_url="old")
    system.next_due = datetime.now(UTC) + timedelta(days=1)
    system.etags["old"] = "old"
    poller.states[system.system_id] = system
    ingest = Mock()
    monkeypatch.setattr(poller, "ingest_system", ingest)
    poller.poll_due_systems()
    assert ingest.call_count == 1
    updated = poller.states["lyft_nyc"]
    assert updated.discovery_url != "old"
    assert not updated.etags


def test_partition_ddl_runs_once_per_day(poller, monkeypatch):
    ensure = Mock()
    monkeypatch.setattr(StationStateRepository, "ensure_partitions", ensure)
    monkeypatch.setattr(poller, "ingest_system", Mock())
    now = datetime.now(UTC)
    poller.poll_due_systems(now)
    poller.poll_due_systems(now)
    poller.poll_due_systems(now + timedelta(days=1))
    assert ensure.call_count == 2


def test_poll_timeout_respects_ttl_and_system_override():
    poller = Poller(None, None, Settings(_env_file=None, poll_timeout_s=30))
    system = state()
    now = datetime.now(UTC)
    system.ttl = 600
    poller.schedule_next(system, now, ok=True)
    assert system.next_due == now + timedelta(seconds=600)
    system.poll_interval_s = 60
    poller.schedule_next(system, now, ok=False)
    assert system.next_due == now + timedelta(seconds=60)


def test_304_does_not_refresh_missing_or_quarantined_stations(poller):
    system = state()
    poller.ingest_system(system, datetime.now(UTC))
    with poller.database.session() as session:
        before = dict(
            session.execute(select(StationCurrent.station_id, StationCurrent.checked_at)).all()
        )
    # s2 исчезла, s3 не прошла проверку: свежесть подтверждается только для s1.
    poller.client.etag = "v2"
    stations = poller.client.feeds["station_status"]["data"]["stations"]
    stations.pop(1)
    stations[1]["num_vehicles_available"] = -1
    poller.ingest_system(system, datetime.now(UTC))
    checked_at = datetime.now(UTC) + timedelta(minutes=1)
    poller.ingest_system(system, checked_at)
    with poller.database.session() as session:
        after = dict(
            session.execute(select(StationCurrent.station_id, StationCurrent.checked_at)).all()
        )
    assert after["s1"] == checked_at
    assert after["s2"] == before["s2"]
    assert after["s3"] == before["s3"]
