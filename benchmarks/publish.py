"""Обновления во время нагрузки: 2500 станций, один пакет каждые 5 секунд."""

import time
from datetime import UTC, datetime

from app.core.database import get_database
from app.repositories.station_state import StationStateRepository
from app.schemas.gbfs import StationStatusRow

for iteration in range(30):
    now = datetime.now(UTC)
    rows = [
        StationStatusRow(
            system_id="m2_benchmark",
            station_id=f"s{i:05}",
            reported_at=now,
            bikes_available=10 + iteration % 2,
            docks_available=20 - iteration % 2,
            is_installed=True,
            is_renting=True,
            is_returning=True,
        )
        for i in range(2500)
    ]
    started = time.perf_counter()
    with get_database().transaction() as session:
        written = StationStateRepository(session).write_status(rows, "m2_benchmark", now)
    print(f"rows={written} seconds={time.perf_counter() - started:.3f}", flush=True)
    time.sleep(5)
