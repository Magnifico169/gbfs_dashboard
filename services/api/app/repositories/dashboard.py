"""Запросы чтения; ORM-объекты не выходят за время жизни сессии."""

from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import (
    RollupCursor,
    Station,
    StationCurrent,
    StationEvent,
    StationQuarantine,
    StationRollup,
    StationStatus,
    System,
)


def serialize(row) -> dict:
    return {
        column.key: (value.isoformat() if isinstance(value, datetime) else value)
        for column in row.__table__.columns
        if (value := getattr(row, column.key)) is not None
    }


class DashboardRepository:
    def __init__(self, session: Session):
        self.session = session

    def systems(self) -> list[dict]:
        return [serialize(row) for row in self.session.scalars(select(System).order_by(System.id))]

    def catalog(self, system_id: str, limit: int, after: str) -> dict:
        if self.session.get(System, system_id) is None:
            raise LookupError("system_not_found")
        rows = list(
            self.session.scalars(
                select(Station)
                .where(
                    Station.system_id == system_id,
                    Station.valid_to.is_(None),
                    Station.station_id > after,
                )
                .order_by(Station.station_id)
                .limit(limit + 1)
            )
        )
        return {
            "items": [serialize(row) for row in rows[:limit]],
            "next_after": rows[limit - 1].station_id if len(rows) > limit else None,
        }

    def snapshot(self, system_id: str, stale_after_s: int) -> dict:
        system = self.session.get(System, system_id)
        if system is None:
            raise LookupError("system_not_found")
        rejected_at = (
            select(StationQuarantine.fetched_at)
            .where(
                StationQuarantine.system_id == Station.system_id,
                StationQuarantine.station_id == Station.station_id,
            )
            .order_by(StationQuarantine.fetched_at.desc())
            .limit(1)
            .correlate(Station)
            .scalar_subquery()
        )
        query = (
            select(Station, StationCurrent, rejected_at)
            .outerjoin(
                StationCurrent,
                and_(
                    StationCurrent.system_id == Station.system_id,
                    StationCurrent.station_id == Station.station_id,
                ),
            )
            .where(Station.system_id == system_id, Station.valid_to.is_(None))
            .order_by(Station.station_id)
        )
        now = datetime.now(UTC)
        stations = []
        for station, current, rejected in self.session.execute(query):
            data = serialize(station)
            data["status"] = serialize(current) if current else None
            data["age_seconds"] = (
                max(0, (now - min(current.checked_at, current.reported_at)).total_seconds())
                if current and current.reported_at
                else None
            )
            if rejected and (current is None or rejected > current.checked_at):
                data["quality"] = "invalid"
            elif current is None or current.reported_at is None:
                data["quality"] = "unknown"
            elif data["age_seconds"] > stale_after_s:
                data["quality"] = "stale"
            else:
                data["quality"] = "fresh"
            stations.append(data)
        return {
            "system_id": system_id,
            "timezone": system.timezone,
            "as_of": now.isoformat(),
            "stations": stations,
        }

    def require_station(self, system_id: str, station_id: str) -> None:
        if (
            self.session.scalar(
                select(Station.id)
                .where(Station.system_id == system_id, Station.station_id == station_id)
                .limit(1)
            )
            is None
        ):
            raise LookupError("station_not_found")

    def history(self, system_id, station_id, start, end, limit, before):
        self.require_station(system_id, station_id)
        query = select(StationStatus).where(
            StationStatus.system_id == system_id,
            StationStatus.station_id == station_id,
            StationStatus.fetched_at >= start,
            StationStatus.fetched_at < end,
        )
        if before:
            query = query.where(StationStatus.fetched_at < before)
        rows = list(
            self.session.scalars(query.order_by(StationStatus.fetched_at.desc()).limit(limit + 1))
        )
        return {
            "items": [serialize(row) for row in rows[:limit]],
            "next_before": rows[limit - 1].fetched_at.isoformat() if len(rows) > limit else None,
        }

    def events(self, system_id, station_id, limit, before):
        self.require_station(system_id, station_id)
        query = select(StationEvent).where(
            StationEvent.system_id == system_id, StationEvent.station_id == station_id
        )
        if before:
            query = query.where(StationEvent.id < before)
        rows = list(self.session.scalars(query.order_by(StationEvent.id.desc()).limit(limit + 1)))
        return {
            "items": [serialize(row) for row in rows[:limit]],
            "next_before": rows[limit - 1].id if len(rows) > limit else None,
            "movement_estimate": "lower_bound",
        }

    def rollups(self, system_id, station_id, start, end, resolution):
        self.require_station(system_id, station_id)
        system = self.session.get(System, system_id)
        query = (
            select(StationRollup)
            .where(
                StationRollup.system_id == system_id,
                StationRollup.station_id == station_id,
                StationRollup.resolution == resolution,
                StationRollup.bucket_end > start,
                StationRollup.bucket_start < end,
            )
            .order_by(StationRollup.bucket_start)
            .limit(1001)
        )
        rows = list(self.session.scalars(query))
        if len(rows) > 1000:
            raise ValueError("too_many_buckets: выберите более крупный resolution")
        cursor = self.session.get(RollupCursor, system_id)
        return {
            "items": [serialize(row) for row in rows],
            "resolution": resolution,
            "timezone": system.timezone,
            "movement_estimate": "lower_bound",
            "bounds": "whole_intersecting_buckets",
            "processed_until": cursor.processed_until.isoformat() if cursor else None,
        }
