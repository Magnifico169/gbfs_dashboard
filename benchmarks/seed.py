"""Синтетический набор M2; запускать с PG* отдельной тестовой базы."""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert

from app.core.config.settings import get_settings
from app.core.database import get_database
from app.models import StationRollup
from app.repositories.station_state import StationStateRepository
from app.schemas.gbfs import StationStatusRow
from app.services.ingestion import IngestService

SYSTEM = "m2_benchmark"
now = datetime.now(UTC)
database = get_database()
info = [
    {"station_id": f"s{i:05}", "name": f"Benchmark {i}", "lat": 40.7, "lon": -74.0, "capacity": 30}
    for i in range(2500)
]
with database.transaction() as session:
    IngestService(session, get_settings()).apply_reference(
        SYSTEM,
        "3.0",
        {
            "system_information": {"data": {"name": "M2 benchmark", "timezone": "Europe/Paris"}},
            "station_information": {"data": {"stations": info}},
        },
    )
    StationStateRepository(session).ensure_partitions(2)
rows = [
    StationStatusRow(
        system_id=SYSTEM,
        station_id=item["station_id"],
        reported_at=now,
        bikes_available=10,
        docks_available=20,
        is_installed=True,
        is_renting=True,
        is_returning=True,
    )
    for item in info
]
started = time.perf_counter()
with database.transaction() as session:
    StationStateRepository(session).write_status(rows, SYSTEM, now)
print(f"2500 initial states: {time.perf_counter() - started:.3f}s")
# Год дневных агрегатов для измерения API; сами вычисления проверяются тестами R.
end = now.replace(hour=0, minute=0, second=0, microsecond=0)
values = [
    {
        "system_id": SYSTEM,
        "station_id": "s00000",
        "resolution": "day",
        "bucket_start": end - timedelta(days=i),
        "bucket_end": end - timedelta(days=i - 1),
        "observed_seconds": 86400,
        "bikes_seconds": 864000,
        "docks_seconds": 1728000,
        "docks_observed_seconds": 86400,
        "empty_seconds": 0,
        "full_seconds": 0,
        "bikes_min": 10,
        "bikes_max": 10,
        "docks_min": 20,
        "docks_max": 20,
        "samples": 100,
        "bikes_added": 50,
        "bikes_removed": 50,
    }
    for i in range(1, 366)
]
with database.transaction() as session:
    session.execute(insert(StationRollup), values)
print("Seeded 2500 stations and 365 daily buckets")
