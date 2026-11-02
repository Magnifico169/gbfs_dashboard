"""Справочники: системы, регионы, типы транспорта, станции (SCD2)."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Region, Station, System, VehicleType
from app.schemas.gbfs import RegionRow, StationInfoRow, SystemInfo, VehicleTypeRow

STATION_ATTRIBUTES = ("name", "short_name", "lat", "lon", "address", "region_id", "capacity")


class ReferenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_system(self, info: SystemInfo, gbfs_version: str) -> None:
        self.session.merge(
            System(
                system_id=info.system_id,
                name=info.name or info.system_id,
                operator=info.operator,
                timezone=info.timezone or "UTC",
                language=info.language,
                gbfs_version=gbfs_version,
                updated_at=datetime.now(UTC),
            )
        )

    def upsert_regions(self, regions: list[RegionRow]) -> None:
        for region in regions:
            self.session.merge(Region(**region.model_dump()))

    def upsert_vehicle_types(self, vehicle_types: list[VehicleTypeRow]) -> None:
        for vehicle_type in vehicle_types:
            self.session.merge(VehicleType(**vehicle_type.model_dump()))

    def sync_stations(self, stations: list[StationInfoRow], system_id: str, now: datetime) -> int:
        """SCD2: изменившуюся или пропавшую из фида станцию закрываем, изменившуюся открываем
        заново. Возвращает число закрытых версий.
        """
        if not stations:
            return 0
        current = {
            station.station_id: station
            for station in self.session.scalars(
                select(Station).where(Station.system_id == system_id, Station.valid_to.is_(None))
            )
        }
        feed_ids = {station.station_id for station in stations}
        to_open, closed = [], 0
        for station in stations:
            existing = current.get(station.station_id)
            if existing is not None:
                if all(getattr(existing, a) == getattr(station, a) for a in STATION_ATTRIBUTES):
                    continue
                existing.valid_to = now
                closed += 1
            to_open.append(station)
        for station_id, existing in current.items():
            if station_id not in feed_ids:
                existing.valid_to = now
                closed += 1
        self.session.flush()  # старые версии закрываем до вставки новых: индекс по текущим уникален
        self.session.add_all(Station(**s.model_dump(), valid_from=now) for s in to_open)
        return closed

    def capacity_by_station(self, system_id: str) -> dict[str, int]:
        rows = self.session.execute(
            select(Station.station_id, Station.capacity).where(
                Station.system_id == system_id,
                Station.valid_to.is_(None),
                Station.capacity.is_not(None),
            )
        )
        return dict(rows.all())
