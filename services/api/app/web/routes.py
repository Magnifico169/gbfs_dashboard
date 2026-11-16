"""Jinja/HTMX-интерфейс использует тот же сервис чтения и RLS, что JSON API."""

import csv
import io
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select

from app.api.auth import data_access, live_access
from app.api.routes.dashboard import stream
from app.core.config.settings import get_settings
from app.core.database import get_database
from app.models import Station, StationEvent
from app.repositories.dashboard import serialize
from app.schemas.auth import Login, Register
from app.services import auth, registration
from app.services.reporting import parse_period

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["query"] = lambda value: urlencode(value)
templates.env.filters["number"] = lambda value: "—" if value is None else f"{value:,.1f}"
templates.env.filters["clock"] = lambda value, zone="UTC": (
    datetime.fromisoformat(value).astimezone(ZoneInfo(zone)).strftime("%d.%m.%Y %H:%M:%S %Z")
    if value
    else "нет данных"
)

ERRORS = {
    "username_taken": "Этот логин уже занят. Выберите другой.",
    "registration_rate_limited": "Слишком много попыток регистрации или входа. Подождите минуту.",
    "invalid_credentials": "Неверный логин или пароль, либо сеанс истёк.",
    "scope_required": (
        "Зона не назначена или система не выбрана. Укажите свою систему; "
        "назначение зоны выполняет администратор."
    ),
    "scope_empty": (
        "В назначенной зоне нет доступных регионов. "
        "Обратитесь к администратору или войдите в другую систему."
    ),
    "system_forbidden": "Эта система недоступна вашей учётной записи.",
    "role_forbidden": "Этот экран недоступен для вашей роли.",
    "station_not_found": "Станция не найдена в вашей зоне.",
    "system_not_found": "Система не найдена. Проверьте её идентификатор.",
    "database_unavailable": "База данных временно недоступна. Повторите запрос позже.",
    "overloaded": "Сервис занят. Повторите запрос через несколько секунд.",
    "login_rate_limited": "Слишком много попыток входа. Подождите минуту.",
    "auth_not_configured": "Сервис входа не настроен. Обратитесь к администратору.",
}


def is_browser(request):
    return request.url.path.startswith("/ui/") or request.url.path in ("/", "/login", "/register")


def error_page(request, status, code, headers=None):
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "code": code,
            "message": ERRORS.get(str(code), str(code)),
            "status": status,
            "fragment": request.headers.get("HX-Request") == "true",
            "expired": status == 401
            and request.url.path.startswith("/ui/")
            and bool(request.cookies.get("gbfs_refresh")),
        },
        status_code=status,
        headers=headers,
    )


def user(request: Request):
    token = request.cookies.get("gbfs_access")
    if not token:
        raise auth.invalid_credentials()
    return auth.decode_token(token, get_settings())


User = Annotated[auth.Principal, Depends(user)]


def cookies(response, pair, request):
    secure = request.url.scheme == "https"
    response.set_cookie(
        "gbfs_access",
        pair.access_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
        max_age=pair.expires_in,
    )
    response.set_cookie(
        "gbfs_refresh",
        pair.refresh_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/ui/",
        max_age=get_settings().auth_refresh_ttl_s,
    )


@router.get("/")
@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    try:
        body = Login.model_validate(
            {
                "username": form.get("username"),
                "password": form.get("password"),
                "system_id": form.get("system_id") or None,
            }
        )
    except ValidationError:
        raise HTTPException(422, "Проверьте логин, пароль и идентификатор системы") from None
    from starlette.concurrency import run_in_threadpool

    def authenticate():
        with get_database().transaction() as session:
            return auth.login(
                session,
                body.username,
                body.password.get_secret_value(),
                body.system_id,
                get_settings(),
            )

    pair = await run_in_threadpool(authenticate)
    principal = auth.decode_token(pair.access_token, get_settings())
    destination = "/ui/manager" if principal.role == "manager" else "/ui/operator"
    response = RedirectResponse(destination, 303)
    cookies(response, pair, request)
    return response


@router.post("/ui/refresh")
def refresh(request: Request):
    token = request.cookies.get("gbfs_refresh")
    if not token:
        raise auth.invalid_credentials()
    with get_database().transaction() as session:
        pair = auth.refresh(session, token, get_settings())
    response = Response(status_code=204)
    cookies(response, pair, request)
    return response


@router.post("/ui/logout")
def logout(request: Request):
    token = request.cookies.get("gbfs_refresh")
    if token:
        with get_database().transaction() as session:
            auth.logout(session, token)
    response = RedirectResponse("/login", 303)
    response.delete_cookie("gbfs_access", path="/")
    response.delete_cookie("gbfs_refresh", path="/ui/")
    return response


async def context(request, principal, system_id, role):
    if principal.role not in (role, "admin"):
        raise HTTPException(403, "role_forbidden")
    access = data_access(principal, system_id)
    reads = request.app.state.reads
    systems = await reads.read(
        ("*", "systems-ui", access), lambda: reads.query("systems", access=access)
    )
    selected = system_id or principal.system_id or (systems[0]["id"] if systems else None)
    if not selected:
        raise HTTPException(404, "system_not_found")
    access = data_access(principal, selected)
    system = next((row for row in systems if row["id"] == selected), None)
    if system is None:
        raise HTTPException(404, "system_not_found")
    return {"user": principal, "system": system, "systems": systems, "access": access}


def station_view(row):
    status = row.get("status") or {}
    quality = row["quality"]
    issue = {
        "invalid": "Данные недостоверны",
        "unknown": "Нет данных",
        "stale": "Потеряна связь",
    }.get(quality)
    if not issue:
        if not status.get("is_installed") or not status.get("is_renting"):
            issue = "Аренда недоступна"
        elif status.get("bikes_available") == 0:
            issue = "Нет транспорта"
        elif status.get("docks_available") == 0:
            issue = "Нет свободных мест"
        elif (status.get("bikes_disabled") or 0) + (status.get("docks_disabled") or 0):
            issue = "Есть неисправные"
    return {
        **row,
        "issue": issue,
        "state": status,
        "priority": {"invalid": 0, "unknown": 1, "stale": 2}.get(quality, 3 if issue else 4),
    }


@router.get("/ui/operator")
async def operator(
    request: Request,
    principal: User,
    system_id: str | None = None,
    q: str = "",
    problems: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
):
    ctx = await context(request, principal, system_id, "operator")
    system_id = ctx["system"]["id"]
    snapshot = await request.app.state.reads.snapshot(system_id, ctx["access"])
    rows = [station_view(row) for row in snapshot["stations"]]
    fresh = [row for row in rows if row["quality"] == "fresh"]
    selected = [
        row
        for row in rows
        if (not problems or row["issue"])
        and q.casefold() in f"{row['name']} {row.get('address', '')} {row['station_id']}".casefold()
    ]
    selected.sort(key=lambda row: (row["priority"], row["name"]))
    ctx.update(
        snapshot=snapshot,
        q=q,
        problems=problems,
        page=page,
        rows=selected[(page - 1) * 100 : page * 100],
        count=len(rows),
        matched=len(selected),
        available=sum(
            row["state"]["bikes_available"]
            for row in fresh
            if row["state"].get("is_installed") and row["state"].get("is_renting")
        ),
        problem_count=sum(bool(row["issue"]) for row in rows),
        stale_count=sum(row["quality"] != "fresh" for row in rows),
        map_data=[
            {k: row.get(k) for k in ("station_id", "name", "lat", "lon", "quality", "issue")}
            for row in selected
        ],
    )

    def zone_events():
        with get_database().session() as session:
            ctx["access"].apply(session)
            return [
                serialize(row)
                for row in session.scalars(
                    select(StationEvent)
                    .where(StationEvent.system_id == system_id)
                    .order_by(StationEvent.id.desc())
                    .limit(20)
                )
            ]

    ctx["events"] = await request.app.state.reads.read(
        (system_id, "zone-events", ctx["access"]), zone_events
    )
    template = (
        "operator_board.html"
        if request.headers.get("HX-Request")
        and not request.headers.get("HX-History-Restore-Request")
        else "operator.html"
    )
    return templates.TemplateResponse(request, template, ctx)


def distance(a, b):
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat, dlon = lat2 - lat1, math.radians(b["lon"] - a["lon"])
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(value)))


@router.get("/ui/operator/station")
async def station(
    request: Request,
    principal: User,
    station_id: str,
    system_id: str | None = None,
    before: Annotated[int | None, Query(gt=0)] = None,
):
    ctx = await context(request, principal, system_id, "operator")
    system_id, access = ctx["system"]["id"], ctx["access"]
    reads = request.app.state.reads
    snapshot = await reads.snapshot(system_id, access)
    row = next((row for row in snapshot["stations"] if row["station_id"] == station_id), None)
    if row is None:
        raise HTTPException(404, "station_not_found")
    nearby = [
        {**station_view(other), "distance": distance(row, other)}
        for other in snapshot["stations"]
        if other["station_id"] != station_id
        and other["quality"] == "fresh"
        and other["status"]["bikes_available"] > 0
        and other["status"]["is_renting"]
        and other["status"]["is_installed"]
    ]
    nearby.sort(key=lambda item: item["distance"])
    end = datetime.now(UTC)
    start = end - timedelta(days=1)
    rollups = await reads.read(
        (system_id, "rollups", station_id, start, end, access),
        lambda: reads.query(
            "rollups", system_id, station_id, start, end, "10m", access=access, report=True
        ),
    )
    events = await reads.read(
        (system_id, "events", station_id, before, access),
        lambda: reads.query("events", system_id, station_id, 50, before, access=access),
    )
    ctx.update(
        station=station_view(row),
        snapshot=snapshot,
        nearby=nearby[:5],
        rollups=rollups,
        events=events,
    )
    return templates.TemplateResponse(request, "station.html", ctx)


@router.get("/ui/stream")
async def ui_stream(request: Request, principal: User, system_id: str):
    access = live_access(principal, system_id)
    return await stream(request, system_id, access, principal)


async def report_context(request, principal, system_id, station_id, start, end, preset, compare):
    ctx = await context(request, principal, system_id, "manager")
    system_id, access = ctx["system"]["id"], ctx["access"]
    left, right = parse_period(start, end, ctx["system"]["timezone"], preset)
    reads = request.app.state.reads

    async def load(a, b):
        return await reads.read(
            (system_id, "report", station_id, a, b, access),
            lambda: reads.query(
                "report", system_id, a, b, station_id or None, access=access, report=True
            ),
        )

    report = await load(left, right)
    previous = await load(left - (right - left), left) if compare else None

    def catalog():
        with get_database().session() as session:
            access.apply(session)
            return [
                serialize(row)
                for row in session.scalars(
                    select(Station)
                    .where(Station.system_id == system_id, Station.valid_to.is_(None))
                    .order_by(Station.name)
                )
            ]

    ctx.update(
        report=report,
        previous=previous,
        station_id=station_id or "",
        compare=compare,
        catalog=await reads.read((system_id, "report-catalog", access), catalog),
        start=left.isoformat(),
        end=right.isoformat(),
        preset=preset,
    )
    regions = {}
    for row in ctx["catalog"]:
        name = row.get("region_id") or "Без региона"
        region = regions.setdefault(name, {"count": 0, "capacity": 0})
        region["count"] += 1
        region["capacity"] += row.get("capacity") or 0
    ctx["regions"] = regions
    return ctx


@router.get("/ui/manager")
async def manager(
    request: Request,
    principal: User,
    system_id: str | None = None,
    station_id: str = "",
    start: str = "",
    end: str = "",
    preset: str = "month",
    compare: bool = False,
):
    ctx = await report_context(
        request, principal, system_id, station_id, start, end, preset, compare
    )
    return templates.TemplateResponse(request, "manager.html", ctx)


@router.get("/ui/report.csv")
async def report_csv(
    request: Request,
    principal: User,
    system_id: str | None = None,
    station_id: str = "",
    start: str = "",
    end: str = "",
    preset: str = "month",
):
    ctx = await report_context(request, principal, system_id, station_id, start, end, preset, False)
    report = ctx["report"]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "system",
            "station",
            "timezone",
            "requested_start",
            "requested_end",
            "resolution",
            "processed_until",
            "movement_estimate",
            "bounds",
        ]
    )

    def safe(value):
        value = str(value or "")
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value

    writer.writerow(
        [
            safe(ctx["system"]["id"]),
            safe(station_id),
            ctx["system"]["timezone"],
            report["start"],
            report["end"],
            report["resolution"],
            report["processed_until"],
            "lower_bound",
            "clipped_10m_edges; partial buckets estimated",
        ]
    )
    fields = [
        "bucket_start",
        "bucket_end",
        "estimated",
        "observed_seconds",
        "bikes_mean",
        "docks_mean",
        "empty_seconds",
        "full_seconds",
        "bikes_added",
        "bikes_removed",
    ]
    writer.writerow(fields)
    writer.writerows([[row.get(key, "") for key in fields] for row in report["series"]])
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gbfs-report.csv"'},
    )


@router.get("/register")
def register_page(request: Request):
    with get_database().transaction() as session:
        systems = registration.systems(session)
    return templates.TemplateResponse(request, "register.html", {"systems": systems, "values": {}})


@router.post("/register")
async def register_submit(request: Request):
    from starlette.concurrency import run_in_threadpool

    form = await request.form()
    # Сохраняются только несекретные поля для повторного заполнения формы.
    values = {
        "username": str(form.get("username", ""))[:100],
        "system_id": str(form.get("system_id", ""))[:200],
    }
    error, status = None, 422
    body = None
    try:
        body = Register.model_validate(
            {
                "username": form.get("username"),
                "system_id": form.get("system_id"),
                "password": form.get("password"),
            }
        )
    except ValidationError:
        error = (
            "Логин: 1–100 символов (латинские буквы, цифры, _, ., @, -). "
            "Пароль: от 12 до 1024 символов. Выберите систему."
        )
    if body is not None and body.password.get_secret_value() != form.get("password_confirm"):
        error = "Пароли не совпадают. Введите их ещё раз."

    def create_account():
        with get_database().transaction() as session:
            return registration.register(session, body, get_settings())

    if error is None:
        try:
            pair = await run_in_threadpool(create_account)
        except HTTPException as exc:
            if exc.detail not in ("username_taken", "system_not_found"):
                raise
            error, status = ERRORS[exc.detail], exc.status_code
        else:
            response = RedirectResponse("/ui/operator", 303)
            cookies(response, pair, request)
            return response

    def choices():
        with get_database().transaction() as session:
            return registration.systems(session)

    systems = await run_in_threadpool(choices)
    return templates.TemplateResponse(
        request,
        "register.html",
        {
            "systems": systems,
            "values": values,
            "error": error,
        },
        status_code=status,
    )
