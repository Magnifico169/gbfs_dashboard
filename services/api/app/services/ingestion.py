"""Приём данных: применение фидов к БД (IngestService) и опрос по расписанию (Poller).

IngestService принимает уже разобранный JSON и не ходит в сеть, поэтому проверяется на
сохранённых фикстурах. Журнал запусков пишется отдельной транзакцией, чтобы ошибка записи
данных не теряла запись о запуске.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.clients.gbfs import GbfsClient
from app.core.config.settings import Settings
from app.core.database import Database
from app.enums import IngestStatus
from app.repositories.ingest import IngestRepository
from app.repositories.reference import ReferenceRepository
from app.repositories.station_state import StationStateRepository
from app.schemas.gbfs import VehicleTypeRow
from app.schemas.ingest import FetchResult, ReferenceResult, StatusCounts
from app.schemas.types import to_int
from app.services import normalization
from app.services.validation import validate_station_status

log = logging.getLogger("gbfs_ingest")

REFERENCE_FEEDS = ("system_information", "station_information", "vehicle_types", "system_regions")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class IngestService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.settings = settings
        self.reference = ReferenceRepository(session)
        self.state = StationStateRepository(session)

    def apply_reference(
        self,
        system_id: str,
        version: str,
        feeds: dict[str, dict[str, Any]],
        now: datetime | None = None,
    ) -> ReferenceResult:
        now = now or datetime.now(UTC)
        self.reference.upsert_system(
            normalization.normalize_system_information(feeds["system_information"], system_id),
            version,
        )
        stations = normalization.normalize_station_information(
            feeds["station_information"], system_id
        )
        self.reference.sync_stations(stations, system_id, now)

        vehicle_types = None
        if "vehicle_types" in feeds:
            vehicle_types = normalization.normalize_vehicle_types(feeds["vehicle_types"], system_id)
            self.reference.upsert_vehicle_types(vehicle_types)
        if "system_regions" in feeds:
            self.reference.upsert_regions(
                normalization.normalize_regions(feeds["system_regions"], system_id)
            )
        return ReferenceResult(stations=len(stations), vehicle_types=vehicle_types)

    def apply_station_status(
        self,
        system_id: str,
        feed: dict[str, Any],
        vehicle_types: list[VehicleTypeRow] | None = None,
        fetched_at: datetime | None = None,
    ) -> StatusCounts:
        fetched_at = fetched_at or datetime.now(UTC)
        status = normalization.normalize_station_status(
            feed, system_id, vehicle_types, self.settings.electric_propulsion_types
        )
        reasons = validate_station_status(
            status,
            self.reference.capacity_by_station(system_id),
            fetched_at,
            self.settings.future_tolerance_s,
        )
        rejected = [(row, reason) for row, reason in zip(status, reasons, strict=True) if reason]
        accepted = [row for row, reason in zip(status, reasons, strict=True) if not reason]
        self.state.quarantine(rejected, fetched_at)
        written = self.state.write_status(accepted, system_id, fetched_at)
        return StatusCounts(
            rows_in=len(status), rows_written=written, rows_quarantined=len(rejected)
        )


class SystemState(BaseModel):
    """Состояние опроса одной системы между циклами."""

    system_id: str
    discovery_url: str
    poll_interval_s: int | None = None
    version: str | None = None
    feed_urls: dict[str, str] = Field(default_factory=dict)
    vehicle_types: list[VehicleTypeRow] | None = None
    etags: dict[str, str] = Field(default_factory=dict)
    reference_at: datetime = EPOCH
    next_due: datetime = EPOCH
    failures: int = 0
    ttl: int = 0


class Poller:
    def __init__(self, database: Database, client: GbfsClient, settings: Settings) -> None:
        self.database = database
        self.client = client
        self.settings = settings
        self.states: dict[str, SystemState] = {}

    def _log_run(self, state: SystemState, feed: str, started_at: datetime, **kwargs: Any) -> None:
        with self.database.transaction() as session:
            IngestRepository(session).log_run(state.system_id, feed, started_at, **kwargs)

    def _fetch(self, state: SystemState, feed: str, url: str) -> FetchResult:
        """Забирает фид и пишет запуск в журнал; ошибка возвращается значением."""
        started_at = datetime.now(UTC)
        result = self.client.fetch_json(url, state.etags.get(url))
        if result.etag:
            state.etags[url] = result.etag
        if result.error:
            status = IngestStatus.ERROR
        elif result.status == status.HTTP_304_NOT_MODIFIED:
            status = IngestStatus.NOT_MODIFIED
        else:
            status = IngestStatus.OK
        # успешный station_status пишется вместе с данными, с числом строк
        if feed != "station_status" or status != IngestStatus.OK:
            self._log_run(
                state,
                feed,
                started_at,
                status=status,
                http_status=result.status,
                error=result.error,
            )
        return result

    def refresh_reference(self, state: SystemState, now: datetime) -> None:
        discovery = self._fetch(state, "gbfs", state.discovery_url)
        if discovery.body is None:
            raise RuntimeError(f"discovery: {discovery.error or 'пустой ответ'}")
        parsed = normalization.parse_discovery(discovery.body)
        state.version, state.feed_urls = parsed.version, parsed.feeds

        feeds = {}
        for feed in REFERENCE_FEEDS:
            if feed in parsed.feeds:
                result = self._fetch(state, feed, parsed.feeds[feed])
                if result.body is not None:
                    feeds[feed] = result.body
        if "system_information" not in feeds or "station_information" not in feeds:
            raise RuntimeError("справочные фиды недоступны")
        with self.database.transaction() as session:
            reference = IngestService(session, self.settings).apply_reference(
                state.system_id, state.version or "", feeds, now
            )
        state.vehicle_types = reference.vehicle_types
        state.reference_at = now
        log.info("%s: справочники обновлены, станций %d", state.system_id, reference.stations)

    def ingest_system(self, state: SystemState, now: datetime) -> None:
        """Один цикл по системе: при необходимости обновляет справочники, затем снимок."""
        refresh = timedelta(seconds=self.settings.reference_refresh_s)
        if now - state.reference_at > refresh or not state.feed_urls:
            self.refresh_reference(state, now)
        url = state.feed_urls.get("station_status")
        if url is None:
            raise RuntimeError("в фиде нет station_status")

        started_at = datetime.now(UTC)
        result = self._fetch(state, "station_status", url)
        if result.error:
            raise RuntimeError(f"station_status: {result.error}")
        if result.status == status.HTTP_304_NOT_MODIFIED or result.body is None:
            return
        try:
            with self.database.transaction() as session:
                counts = IngestService(session, self.settings).apply_station_status(
                    state.system_id, result.body, state.vehicle_types
                )
                IngestRepository(session).log_run(
                    state.system_id,
                    "station_status",
                    started_at,
                    IngestStatus.OK,
                    result.status,
                    counts.rows_in,
                    counts.rows_written,
                    counts.rows_quarantined,
                )
        except Exception as exc:
            self._log_run(
                state,
                "station_status",
                started_at,
                status=IngestStatus.ERROR,
                error=f"запись: {exc}"[:500],
            )
            raise
        state.ttl = to_int(result.body.get("ttl")) or 0
        log.info(
            "%s: принято %d, записано %d, в карантине %d",
            state.system_id,
            counts.rows_in,
            counts.rows_written,
            counts.rows_quarantined,
        )

    def schedule_next(self, state: SystemState, now: datetime, ok: bool) -> None:
        cfg = self.settings
        if ok:
            state.failures = 0
            ttl = min(max(state.ttl, cfg.poll_interval_min_s), cfg.poll_interval_max_s)
            interval = state.poll_interval_s or ttl
        else:
            state.failures += 1
            interval = min(cfg.poll_interval_min_s * 2**state.failures, cfg.poll_backoff_max_s)
        state.next_due = now + timedelta(seconds=interval)

    def poll_due_systems(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        with self.database.transaction() as session:
            StationStateRepository(session).ensure_partitions(self.settings.partitions_days_ahead)
            settings = [
                (s.system_id, s.discovery_url, s.poll_interval_s)
                for s in IngestRepository(session).enabled_systems()
            ]
        for system_id, discovery_url, poll_interval_s in settings:
            state = self.states.setdefault(
                system_id, SystemState(system_id=system_id, discovery_url=discovery_url)
            )
            state.poll_interval_s = poll_interval_s
            if now < state.next_due:
                continue
            try:
                self.ingest_system(state, now)
                ok = True
            except Exception as exc:
                log.error("%s: ошибка: %s", system_id, exc)
                ok = False
            self.schedule_next(state, datetime.now(UTC), ok)
