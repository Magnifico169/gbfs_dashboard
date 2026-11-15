"""Ограниченный TTL-кэш с объединением одинаковых запросов и отдельным пулом отчётов."""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable

from app.core.config.settings import Settings
from app.core.database import Database
from app.repositories.dashboard import DashboardRepository
from app.services.access import Access


class ReadCapacityError(Exception):
    pass


class ReadService:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.reports = Database(settings, pool_size=2, max_overflow=0)
        self.settings = settings
        self.cache: OrderedDict[tuple, tuple[float, object]] = OrderedDict()
        self.pending: dict[tuple, asyncio.Task] = {}
        self.generation = 0

    def invalidate(self, system_id: str) -> None:
        self.generation += 1
        for key in list(self.cache):
            if key[0] in (system_id, "*"):
                del self.cache[key]

    async def read(self, key: tuple, loader: Callable):
        cached = self.cache.get(key)
        if cached and cached[0] > time.monotonic():
            self.cache.move_to_end(key)
            return cached[1]
        task = self.pending.get(key)
        if task is None:
            is_report = key[1] in ("history", "rollups")
            active = sum(
                (pending[1] in ("history", "rollups")) == is_report for pending in self.pending
            )
            capacity = 2 if is_report else self.settings.read_concurrency
            if active >= capacity:
                raise ReadCapacityError
            task = asyncio.create_task(self._load(key, loader, self.generation))
            self.pending[key] = task
        return await asyncio.shield(task)

    async def _load(self, key, loader, generation):
        try:
            result = await asyncio.to_thread(loader)
            if generation == self.generation:
                self.cache[key] = (time.monotonic() + self.settings.read_cache_ttl_s, result)
                self.cache.move_to_end(key)
                while len(self.cache) > self.settings.read_cache_size:
                    self.cache.popitem(last=False)
            return result
        finally:
            self.pending.pop(key, None)

    def query(self, method: str, *args, access: Access, report=False):
        database = self.reports if report else self.database
        with database.session() as session:
            access.apply(session)
            return getattr(DashboardRepository(session), method)(*args)

    async def snapshot(self, system_id: str, access: Access):
        return await self.read(
            (system_id, "snapshot", access),
            lambda: self.query("snapshot", system_id, self.settings.stale_after_s, access=access),
        )

    async def close(self):
        if self.pending:
            await asyncio.gather(*self.pending.values(), return_exceptions=True)
        self.reports.dispose()
