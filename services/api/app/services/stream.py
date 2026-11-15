"""Один LISTEN на процесс, ограниченные очереди клиентов и восстановление через TTL."""

import asyncio
import json
import logging
from collections import defaultdict
from contextlib import suppress

import psycopg

from app.core.config.settings import Settings
from app.services.dashboard import ReadCapacityError, ReadService

log = logging.getLogger(__name__)


def encode_event(event: str, data: dict) -> str:
    return (
        f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


class StreamService:
    def __init__(self, reads: ReadService, settings: Settings):
        self.reads, self.settings = reads, settings
        self.clients: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self.snapshots: dict[str, dict] = {}
        self.initial_messages: dict[str, tuple[str, bytes]] = {}
        self.count = 0
        self.connected = False
        self.tasks = []
        self._publishing: set[str] = set()

    def subscribe(self, system_id: str) -> asyncio.Queue:
        if self.count >= self.settings.sse_max_clients:
            raise ReadCapacityError
        queue = asyncio.Queue(maxsize=self.settings.sse_queue_size)
        self.clients[system_id].add(queue)
        self.count += 1
        return queue

    def unsubscribe(self, system_id: str, queue: asyncio.Queue):
        clients = self.clients.get(system_id)
        if clients is not None and queue in clients:
            clients.remove(queue)
            self.count -= 1
            if not clients:
                self.clients.pop(system_id)
                self.snapshots.pop(system_id, None)
                self.initial_messages.pop(system_id, None)

    def initial_message(self, snapshot: dict) -> bytes:
        system_id = snapshot["system_id"]
        cached = self.initial_messages.get(system_id)
        if cached and cached[0] == snapshot["as_of"]:
            return cached[1]
        if system_id not in self.snapshots:
            self.snapshots[system_id] = {row["station_id"]: row for row in snapshot["stations"]}
        message = ("retry: 3000\n\n" + encode_event("snapshot", snapshot)).encode()
        self.initial_messages[system_id] = (snapshot["as_of"], message)
        return message

    def broadcast(self, system_id: str, event: str, data: dict):
        message = encode_event(event, data)
        for queue in tuple(self.clients.get(system_id, ())):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(encode_event("reset", {"reason": "slow_consumer"}))
            else:
                queue.put_nowait(message)

    async def publish(self, system_id: str):
        if system_id in self._publishing or system_id not in self.clients:
            return
        self._publishing.add(system_id)
        try:
            snapshot = await self.reads.snapshot(system_id)
            current = {row["station_id"]: row for row in snapshot["stations"]}
            previous = self.snapshots.get(system_id, {})

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
                    system_id,
                    "delta",
                    {
                        "system_id": system_id,
                        "as_of": snapshot["as_of"],
                        "stations": changed,
                        "removed": removed,
                    },
                )
            self.snapshots[system_id] = current
        except Exception:
            log.exception("SSE refresh failed for %s", system_id)
            self.broadcast(system_id, "unavailable", {"reason": "database_unavailable"})
        finally:
            self._publishing.discard(system_id)

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
                        await self.publish(system_id)
            except psycopg.Error:
                log.warning("LISTEN disconnected; retrying", exc_info=True)
            finally:
                self.connected = False
            await asyncio.sleep(1)

    async def refresh(self):
        while True:
            await asyncio.sleep(self.settings.sse_heartbeat_s)
            for system_id in tuple(self.clients):
                await self.publish(system_id)

    def start(self):
        self.tasks = [asyncio.create_task(self.listen()), asyncio.create_task(self.refresh())]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
