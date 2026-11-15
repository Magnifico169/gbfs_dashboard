"""Один LISTEN на процесс, ограниченные очереди клиентов и восстановление через TTL."""

import asyncio
import json
import logging
from collections import defaultdict
from contextlib import suppress

import psycopg

from app.core.config.settings import Settings
from app.services.access import Access
from app.services.dashboard import ReadCapacityError, ReadService

log = logging.getLogger(__name__)


def encode_event(event: str, data: dict) -> str:
    return (
        f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


class StreamService:
    def __init__(self, reads: ReadService, settings: Settings):
        self.reads, self.settings = reads, settings
        self.clients: dict[tuple[str, Access], set[asyncio.Queue]] = defaultdict(set)
        self.snapshots: dict[tuple[str, Access], dict] = {}
        self.initial_messages: dict[tuple[str, Access], tuple[str, bytes]] = {}
        self.count = 0
        self.connected = False
        self.tasks = []
        self._publishing: set[tuple[str, Access]] = set()

    def subscribe(self, system_id: str, access: Access) -> asyncio.Queue:
        group = (system_id, access)
        if self.count >= self.settings.sse_max_clients:
            raise ReadCapacityError
        queue = asyncio.Queue(maxsize=self.settings.sse_queue_size)
        self.clients[group].add(queue)
        self.count += 1
        return queue

    def unsubscribe(self, system_id: str, queue: asyncio.Queue, access: Access):
        group = (system_id, access)
        clients = self.clients.get(group)
        if clients is not None and queue in clients:
            clients.remove(queue)
            self.count -= 1
            if not clients:
                self.clients.pop(group)
                self.snapshots.pop(group, None)
                self.initial_messages.pop(group, None)

    def initial_message(self, snapshot: dict, access: Access) -> bytes:
        group = (snapshot["system_id"], access)
        cached = self.initial_messages.get(group)
        if cached and cached[0] == snapshot["as_of"]:
            return cached[1]
        if group not in self.snapshots:
            self.snapshots[group] = {row["station_id"]: row for row in snapshot["stations"]}
        message = ("retry: 3000\n\n" + encode_event("snapshot", snapshot)).encode()
        self.initial_messages[group] = (snapshot["as_of"], message)
        return message

    def broadcast(self, group: tuple[str, Access], event: str, data: dict):
        message = encode_event(event, data)
        for queue in tuple(self.clients.get(group, ())):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(encode_event("reset", {"reason": "slow_consumer"}))
            else:
                queue.put_nowait(message)

    async def publish(self, group: tuple[str, Access]):
        system_id, access = group
        if group in self._publishing or group not in self.clients:
            return
        self._publishing.add(group)
        try:
            snapshot = await self.reads.snapshot(system_id, access)
            if group not in self.clients:
                return
            current = {row["station_id"]: row for row in snapshot["stations"]}
            previous = self.snapshots.get(group, {})

            # Возраст тикает у каждого клиента; изменение чисел/качества шлём один раз на систему.
            def comparable(row):
                return {key: value for key, value in row.items() if key != "age_seconds"}

            changed = [
                row
                for key, row in current.items()
                if key not in previous or comparable(row) != comparable(previous[key])
            ]
            removed = sorted(previous.keys() - current.keys())
            if changed or removed:
                self.broadcast(
                    group,
                    "delta",
                    {
                        "system_id": system_id,
                        "as_of": snapshot["as_of"],
                        "stations": changed,
                        "removed": removed,
                    },
                )
            self.snapshots[group] = current
        except Exception:
            log.exception("SSE refresh failed for %s", system_id)
            self.broadcast(group, "unavailable", {"reason": "database_unavailable"})
        finally:
            self._publishing.discard(group)

    async def listen(self):
        cfg = self.settings
        options = f"-c role={cfg.db_role}" if cfg.db_role else ""
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    host=cfg.pg_host,
                    port=cfg.pg_port,
                    user=cfg.pg_user,
                    password=cfg.pg_password.get_secret_value(),
                    dbname=cfg.pg_database,
                    autocommit=True,
                    connect_timeout=cfg.db_connect_timeout_s,
                    options=options,
                ) as connection:
                    await connection.execute("LISTEN snapshot_ready")
                    self.connected = True
                    async for notification in connection.notifies():
                        try:
                            system_id = json.loads(notification.payload)["system_id"]
                            if not isinstance(system_id, str):
                                continue
                        except (ValueError, KeyError, TypeError):
                            continue
                        self.reads.invalidate(system_id)
                        for group in tuple(self.clients):
                            if group[0] == system_id:
                                await self.publish(group)
            except psycopg.Error:
                log.warning("LISTEN disconnected; retrying", exc_info=True)
            finally:
                self.connected = False
            await asyncio.sleep(1)

    async def refresh(self):
        while True:
            await asyncio.sleep(self.settings.sse_heartbeat_s)
            for group in tuple(self.clients):
                await self.publish(group)

    def start(self):
        self.tasks = [asyncio.create_task(self.listen()), asyncio.create_task(self.refresh())]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
