"""1000 пользователей и повтор с 10000; только изолированная тестовая БД."""

import os
import random
from datetime import UTC, datetime, timedelta

from locust import FastHttpUser, between, task

SYSTEM = os.environ.get("BENCH_SYSTEM", "m2_benchmark")


class DashboardUser(FastHttpUser):
    wait_time = between(5, 30)
    connection_timeout = 5
    network_timeout = 10

    def on_start(self):
        self.auth_headers = {"Authorization": f"Bearer {os.environ['BENCH_TOKEN']}"}

    @task(8)
    def stations(self):
        self.client.get(
            f"/api/v1/systems/{SYSTEM}/stations?limit=100",
            name="stations",
            headers=self.auth_headers,
        )

    @task(2)
    def station(self):
        station_id = f"s{random.randrange(2500):05}"  # noqa: S311
        self.client.get(
            f"/api/v1/systems/{SYSTEM}/stations/{station_id}",
            name="station",
            headers=self.auth_headers,
        )

    @task(1)
    def annual_rollup(self):
        end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        self.client.get(
            f"/api/v1/systems/{SYSTEM}/stations/s00000/rollups",
            params={
                "start": (end - timedelta(days=365)).isoformat(),
                "end": end.isoformat(),
                "resolution": "day",
            },
            name="annual_rollup",
            headers=self.auth_headers,
        )
