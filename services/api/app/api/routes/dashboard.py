"""Чтение и SSE с обязательным контекстом роли и зоны."""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.auth import CurrentUser, data_access, live_access
from app.services.access import Access
from app.services.stream import encode_event

DataAccess = Annotated[Access, Depends(data_access)]
LiveAccess = Annotated[Access, Depends(live_access)]

router = APIRouter(prefix="/api/v1", tags=["dashboard"])
Limit = Annotated[int, Query(ge=1, le=500)]


def interval(start: datetime, end: datetime, days: int):
    if start.tzinfo is None or end.tzinfo is None or not start < end:
        raise HTTPException(422, "start/end должны содержать часовой пояс; start < end")
    if end - start > timedelta(days=days):
        raise HTTPException(422, f"Интервал не больше {days} дней")


@router.get("/systems")
async def systems(request: Request, access: DataAccess):
    reads = request.app.state.reads
    return await reads.read(
        ("*", "systems", access), lambda: JSONResponse(reads.query("systems", access=access))
    )


@router.get("/systems/{system_id}/catalog")
async def catalog(
    request: Request, system_id: str, access: DataAccess, limit: Limit = 100, after: str = ""
):
    reads = request.app.state.reads
    return await reads.read(
        (system_id, "catalog", limit, after, access),
        lambda: JSONResponse(reads.query("catalog", system_id, limit, after, access=access)),
    )


@router.get("/systems/{system_id}/stations")
async def stations(
    request: Request,
    system_id: str,
    access: LiveAccess,
    limit: Limit = 100,
    after: str = "",
    region_id: str | None = None,
):
    snapshot = await request.app.state.reads.snapshot(system_id, access)
    rows = [
        row
        for row in snapshot["stations"]
        if row["station_id"] > after and (region_id is None or row.get("region_id") == region_id)
    ]
    return JSONResponse(
        {
            **snapshot,
            "stations": rows[:limit],
            "next_after": rows[limit - 1]["station_id"] if len(rows) > limit else None,
        }
    )


@router.get("/systems/{system_id}/stations/{station_id}")
async def station(request: Request, system_id: str, station_id: str, access: LiveAccess):
    snapshot = await request.app.state.reads.snapshot(system_id, access)
    row = next((row for row in snapshot["stations"] if row["station_id"] == station_id), None)
    if row is None:
        raise HTTPException(404, "station_not_found")
    return JSONResponse({"as_of": snapshot["as_of"], "station": row})


@router.get("/systems/{system_id}/stations/{station_id}/history")
async def history(
    request: Request,
    system_id: str,
    access: LiveAccess,
    station_id: str,
    start: datetime,
    end: datetime,
    limit: Limit = 100,
    before: datetime | None = None,
):
    interval(start, end, 1 if access.role == "operator" else 90)
    if before and before.tzinfo is None:
        raise HTTPException(422, "before должен содержать часовой пояс")
    reads = request.app.state.reads
    key = (system_id, "history", station_id, start, end, limit, before, access)
    return await reads.read(
        key,
        lambda: JSONResponse(
            reads.query(
                "history",
                system_id,
                station_id,
                start,
                end,
                limit,
                before,
                access=access,
                report=True,
            )
        ),
    )


@router.get("/systems/{system_id}/stations/{station_id}/events")
async def events(
    request: Request,
    system_id: str,
    access: LiveAccess,
    station_id: str,
    limit: Limit = 100,
    before: Annotated[int | None, Query(gt=0)] = None,
):
    reads = request.app.state.reads
    return await reads.read(
        (system_id, "events", station_id, limit, before, access),
        lambda: JSONResponse(
            reads.query("events", system_id, station_id, limit, before, access=access)
        ),
    )


@router.get("/systems/{system_id}/stations/{station_id}/rollups")
async def rollups(
    request: Request,
    system_id: str,
    access: DataAccess,
    station_id: str,
    start: datetime,
    end: datetime,
    resolution: Literal["auto", "10m", "hour", "day"] = "auto",
):
    interval(start, end, 3660)
    if resolution == "auto":
        seconds = (end - start).total_seconds()
        resolution = "10m" if seconds <= 600 * 998 else "hour" if seconds <= 3600 * 998 else "day"
    reads = request.app.state.reads
    return await reads.read(
        (system_id, "rollups", station_id, start, end, resolution, access),
        lambda: JSONResponse(
            reads.query(
                "rollups", system_id, station_id, start, end, resolution, access=access, report=True
            )
        ),
    )


@router.get("/systems/{system_id}/stream")
async def stream(request: Request, system_id: str, access: LiveAccess, user: CurrentUser):
    hub = request.app.state.stream
    queue = hub.subscribe(system_id, access)
    try:
        snapshot = await request.app.state.reads.snapshot(system_id, access)
    except BaseException:
        hub.unsubscribe(system_id, queue, access)
        raise

    async def messages():
        try:
            # Переподключение всегда начинает с актуального снимка: NOTIFY не хранит историю.
            if time.time() >= user.exp:
                yield encode_event("auth_expired", {"reason": "token_expired"})
                return
            yield hub.initial_message(snapshot, access)
            while True:
                remaining = user.exp - time.time()
                if remaining <= 0:
                    yield encode_event("auth_expired", {"reason": "token_expired"})
                    return
                if await request.is_disconnected():
                    return
                try:
                    message = await asyncio.wait_for(
                        queue.get(), min(hub.settings.sse_heartbeat_s, remaining)
                    )
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if time.time() >= user.exp:
                    yield encode_event("auth_expired", {"reason": "token_expired"})
                    return
                yield message
                if "event: reset\n" in message:
                    return
        finally:
            hub.unsubscribe(system_id, queue, access)

    return StreamingResponse(
        messages(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/systems/{system_id}/report")
async def report(
    request: Request,
    system_id: str,
    access: DataAccess,
    start: datetime,
    end: datetime,
    station_id: str | None = None,
):
    interval(start, end, 3660)
    reads = request.app.state.reads
    return await reads.read(
        (system_id, "report", station_id, start, end, access),
        lambda: reads.query(
            "report", system_id, start, end, station_id, access=access, report=True
        ),
    )
