"""Отчёты читают только mart и справочники, с контекстом RLS вызывающего."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Float, and_, cast, func, select, union_all

from app.core.config.settings import get_settings
from app.models import LoadForecast, RollupCursor, Station, StationInsight, StationRollup, System
from app.repositories.dashboard import serialize
from app.services.reporting import SUM_FIELDS, metrics, resolution_for, segments


class ReportRepository:
    def __init__(self, session):
        self.session = session

    def report(self, system_id, start, end, station_id=None):
        system = self.session.get(System, system_id)
        if system is None:
            raise LookupError("system_not_found")
        station = None
        if station_id:
            station = self.session.scalar(
                select(Station).where(
                    Station.system_id == system_id,
                    Station.station_id == station_id,
                    Station.valid_to.is_(None),
                )
            )
            if station is None:
                raise LookupError("station_not_found")
        resolution = resolution_for(start, end)
        r = StationRollup
        pieces = []
        for level, left, right in segments(start, end, resolution, system.timezone):
            lower, upper = func.greatest(r.bucket_start, left), func.least(r.bucket_end, right)
            duration = func.extract("epoch", upper - lower)
            fraction = cast(duration, Float) / func.extract("epoch", r.bucket_end - r.bucket_start)
            query = select(
                r.station_id,
                lower.label("bucket_start"),
                upper.label("bucket_end"),
                (fraction < 1).label("estimated"),
                *[(getattr(r, field) * fraction).label(field) for field in SUM_FIELDS],
            ).where(
                r.system_id == system_id,
                r.resolution == level,
                r.bucket_end > left,
                r.bucket_start < right,
            )
            if station_id:
                query = query.where(r.station_id == station_id)
            pieces.append(query)
        source = union_all(*pieces).subquery()
        sums = [func.sum(source.c[f]).label(f) for f in SUM_FIELDS]
        series = [
            metrics(dict(row))
            for row in self.session.execute(
                select(
                    source.c.bucket_start,
                    source.c.bucket_end,
                    func.bool_or(source.c.estimated).label("estimated"),
                    *sums,
                )
                .group_by(source.c.bucket_start, source.c.bucket_end)
                .order_by(source.c.bucket_start)
            ).mappings()
        ]
        ranking = [
            metrics(dict(row))
            for row in self.session.execute(
                select(
                    source.c.station_id, Station.name, Station.capacity, Station.region_id, *sums
                )
                .join(
                    Station,
                    and_(
                        Station.system_id == system_id,
                        Station.station_id == source.c.station_id,
                        Station.valid_to.is_(None),
                    ),
                )
                .group_by(source.c.station_id, Station.name, Station.capacity, Station.region_id)
            ).mappings()
        ]
        for row in ranking:
            row["per_dock"] = (
                (row["bikes_added"] + row["bikes_removed"]) / row["capacity"]
                if row["capacity"]
                else None
            )
        ranking.sort(
            key=lambda row: (row["per_dock"] is not None, row["per_dock"] or 0), reverse=True
        )
        total = metrics({f: sum(row[f] or 0 for row in series) for f in SUM_FIELDS})
        cursor = self.session.get(RollupCursor, system_id)
        insights = self.session.scalars(
            select(StationInsight).where(StationInsight.system_id == system_id)
        )
        insight_rows = [serialize(row) for row in insights]
        profile = {}
        for row in insight_rows:
            samples = row.pop("profile", [])
            if station_id and row["station_id"] != station_id:
                continue
            for sample in samples:
                key = (sample["weekday"], sample["hour"])
                group = profile.setdefault(
                    key, {"weekday": key[0], "hour": key[1], "weighted": 0, "observed_seconds": 0}
                )
                group["weighted"] += sample["occupancy"] * sample["observed_seconds"]
                group["observed_seconds"] += sample["observed_seconds"]
        profile_rows = [
            {**row, "occupancy": row["weighted"] / row["observed_seconds"]}
            for row in profile.values()
            if row["observed_seconds"] > 0
        ]
        now = datetime.now(UTC)
        for row in insight_rows:
            age = row.get("age_seconds")
            if age is not None:
                row["age_seconds"] = age + max(
                    0, (now - datetime.fromisoformat(row["as_of"])).total_seconds()
                )
                if row["quality"] == "fresh" and row["age_seconds"] > get_settings().stale_after_s:
                    row["quality"] = "stale"
                    row.pop("occupancy", None)
                    row.pop("electric_share", None)
        forecast = None
        if station_id:
            forecast = self.session.scalar(
                select(LoadForecast)
                .where(
                    LoadForecast.system_id == system_id,
                    LoadForecast.station_id == station_id,
                    LoadForecast.anchor <= end,
                    LoadForecast.anchor >= end - timedelta(hours=1),
                )
                .order_by(LoadForecast.anchor.desc())
                .limit(1)
            )
        return {
            "system": serialize(system),
            "station": serialize(station) if station else None,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "resolution": resolution,
            "series": [
                {
                    **row,
                    "bucket_start": row["bucket_start"].isoformat(),
                    "bucket_end": row["bucket_end"].isoformat(),
                }
                for row in series
            ],
            "ranking": ranking,
            "total": total,
            "insights": insight_rows,
            "profile": profile_rows,
            "forecast": serialize(forecast) if forecast else None,
            "processed_until": cursor.processed_until.isoformat() if cursor else None,
            "estimated": any(row["estimated"] for row in series),
            "movement_estimate": "lower_bound",
            "bounds": "clipped_10m_edges",
        }
