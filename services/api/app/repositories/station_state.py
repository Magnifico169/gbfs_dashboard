"""Состояние станций: текущее, факты, карантин, партиции."""

from datetime import datetime, timedelta

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from app.models import StationCurrent, StationEvent, StationQuarantine, StationStatus
from app.repositories.notifications import snapshot_ready
from app.schemas.gbfs import StationStatusRow
from app.services.events import station_events


class StationStateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_partitions(self, days_ahead: int) -> None:
        self.session.execute(select(func.core.ensure_station_status_partitions(days_ahead)))

    def touch_current(
        self, system_id: str, previous_check: datetime | None, checked_at: datetime
    ) -> None:
        if previous_check is None:
            return
        self.session.execute(
            update(StationStatus)
            .where(
                StationStatus.system_id == system_id,
                StationStatus.station_id == StationCurrent.station_id,
                StationStatus.fetched_at == StationCurrent.fetched_at,
                StationCurrent.system_id == system_id,
                StationCurrent.checked_at == previous_check,
                StationCurrent.reported_at.is_not(None),
            )
            .values(
                observed_until=func.greatest(
                    StationStatus.observed_until, func.least(checked_at, StationCurrent.reported_at)
                )
            )
        )
        self.session.execute(
            update(StationCurrent)
            .where(
                StationCurrent.system_id == system_id,
                StationCurrent.checked_at == previous_check,
            )
            .values(checked_at=checked_at)
        )
        snapshot_ready(self.session, system_id)

    def quarantine(self, rows: list[tuple[StationStatusRow, str]], fetched_at: datetime) -> None:
        self.session.add_all(
            StationQuarantine(
                system_id=row.system_id,
                station_id=row.station_id or "",
                fetched_at=fetched_at,
                reason=reason,
                payload=row.model_dump(mode="json"),
            )
            for row, reason in rows
        )

    def write_status(
        self,
        rows: list[StationStatusRow],
        system_id: str,
        fetched_at: datetime,
        stale_after_s: int = 300,
    ) -> int:
        """Факты при изменении или разрыве наблюдений; подтверждения продлевают интервал."""
        if not rows:
            return 0
        columns = StationCurrent.state_columns()
        known = {
            row.station_id: row
            for row in self.session.scalars(
                select(StationCurrent).where(StationCurrent.system_id == system_id)
            )
        }
        facts, events, confirmations = [], [], []
        new_current, changed_current, touched_current = [], [], []
        for row in rows:
            data = row.model_dump()
            previous = known.get(row.station_id)
            observed_until = min(fetched_at, row.reported_at) if row.reported_at else None
            changed = previous is None or any(
                getattr(previous, column) != data[column] for column in columns
            )
            if previous is not None:
                # Счётчики могут совпасть после обрыва связи: это новый интервал наблюдения.
                gap = fetched_at - previous.checked_at > timedelta(seconds=stale_after_s)
                previous_known = previous.reported_at is not None
                changed = changed or gap or previous_known != (row.reported_at is not None)
                if previous.reported_at and row.reported_at:
                    changed = changed or (
                        previous.reported_at < fetched_at - timedelta(seconds=stale_after_s)
                        and row.reported_at >= fetched_at - timedelta(seconds=stale_after_s)
                    )
                events.extend(station_events(previous, row, fetched_at, stale_after_s))
            if changed:
                facts.append({**data, "fetched_at": fetched_at, "observed_until": observed_until})
                stamped = {**data, "fetched_at": fetched_at, "checked_at": fetched_at}
                (new_current if previous is None else changed_current).append(stamped)
            else:
                if observed_until is not None and previous.reported_at is not None:
                    observed_until = max(
                        observed_until, min(previous.checked_at, previous.reported_at)
                    )
                confirmations.append(
                    {
                        "system_id": system_id,
                        "station_id": row.station_id,
                        "fetched_at": previous.fetched_at,
                        "observed_until": observed_until,
                    }
                )
                touched_current.append(
                    {
                        "system_id": system_id,
                        "station_id": row.station_id,
                        "reported_at": row.reported_at,
                        "checked_at": fetched_at,
                    }
                )
        if facts:
            self.session.execute(insert(StationStatus), facts)
        if confirmations:
            self.session.execute(update(StationStatus), confirmations)
        if events:
            self.session.execute(insert(StationEvent), events)
        for statement, values in (
            (insert(StationCurrent), new_current),
            (update(StationCurrent), changed_current),
            (update(StationCurrent), touched_current),
        ):
            if values:
                self.session.execute(statement, values)
        snapshot_ready(self.session, system_id)
        return len(facts)
