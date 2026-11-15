import asyncio
import json
import secrets
import time
from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from test_m2 import row, seed, write

from app.core.config.settings import get_settings
from app.core.database import Database
from app.main import create_app
from app.models import Region, Station, StationCurrent, StationRollup, System
from app.models.auth import AuditLog, AuthSession, UserAccount, UserScope, Zone
from app.services.access import Access
from app.services.auth import passwords, token_hash
from app.services.dashboard import ReadService
from app.services.stream import StreamService

PASSWORD = secrets.token_urlsafe(24)
SYSTEM = "synthetic_v3"
PREFIX = f"/api/v1/systems/{SYSTEM}"


@pytest.fixture
def auth_env(pg_database, monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", secrets.token_urlsafe(48))
    monkeypatch.delenv("DB_ROLE", raising=False)
    get_settings.cache_clear()
    database = Database(get_settings())
    now = seed(database)
    password_hash = passwords.hash(PASSWORD)
    with database.transaction() as session:
        session.add(System(id="other", name="Other", timezone="UTC", gbfs_version="3.0"))
        session.add_all([Region(system_id=SYSTEM, id=r, name=r) for r in ("r1", "r2")])
        station = session.scalar(select(Station).where(Station.station_id == "s2"))
        station.region_id = "r2"
        users = {}
        for name, role in (
            ("admin", "admin"),
            ("north", "operator"),
            ("south", "operator"),
            ("manager", "manager"),
            ("unscoped", "operator"),
            ("empty", "operator"),
        ):
            user = UserAccount(
                username=name, password_hash=password_hash, role_id=role, active=True
            )
            session.add(user)
            session.flush()
            users[name] = user.id
        zones = {}
        for name, regions in (("north", ["r1"]), ("south", ["r2"]), ("empty", [])):
            zone = Zone(system_id=SYSTEM, name=name, region_ids=regions)
            session.add(zone)
            session.flush()
            zones[name] = zone.id
        for name in ("north", "south", "manager", "empty"):
            session.add(
                UserScope(
                    user_id=users[name],
                    system_id=SYSTEM,
                    zone_id=zones["north" if name == "manager" else name],
                )
            )
        for station_id in ("s1", "s2"):
            session.add(
                StationRollup(
                    system_id=SYSTEM,
                    station_id=station_id,
                    resolution="hour",
                    bucket_start=now - timedelta(hours=1),
                    bucket_end=now,
                    observed_seconds=3600,
                    bikes_seconds=18000,
                    docks_seconds=18000,
                    docks_observed_seconds=3600,
                    empty_seconds=0,
                    full_seconds=0,
                    samples=1,
                    bikes_added=0,
                    bikes_removed=0,
                )
            )
    monkeypatch.setenv("DB_ROLE", "app_api")
    get_settings.cache_clear()
    Database.reset_instance()
    with TestClient(create_app()) as client:
        yield client, database, users, zones, now
    database.dispose()


def login(client, username, system_id=SYSTEM):
    result = client.post(
        "/api/v1/auth/login",
        json={
            "username": username,
            "password": PASSWORD,
            "system_id": None if username == "admin" else system_id,
        },
    )
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    return result.json()


def headers(pair):
    return {"Authorization": f"Bearer {pair['access_token']}"}


def params(now):
    return {
        "start": (now - timedelta(hours=2)).isoformat(),
        "end": (now + timedelta(seconds=1)).isoformat(),
    }


def test_login_tokens_rotation_logout_and_local_validation(auth_env):
    client, database, users, _, _ = auth_env
    pair = login(client, "north")
    me = client.get("/api/v1/auth/me", headers=headers(pair))
    assert me.json()["region_ids"] == ["r1"]
    assert me.json()["system_id"] == SYSTEM
    with database.session() as session:
        stored = session.get(AuthSession, token_hash(pair["refresh_token"]))
        assert stored.user_id == users["north"]
        assert session.get(UserAccount, users["north"]).password_hash.startswith("$argon2id$")
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != pair["refresh_token"]
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        ).status_code
        == 401
    )
    current = rotated.json()
    assert (
        client.post(
            "/api/v1/auth/logout", json={"refresh_token": current["refresh_token"]}
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": current["refresh_token"]}
        ).status_code
        == 401
    )
    # Logout отзывает обновление; уже выданный JWT живёт до exp без запроса к БД.
    assert client.get("/api/v1/auth/me", headers=headers(current)).status_code == 200
    with database.transaction() as session:
        session.get(UserAccount, users["north"]).active = False
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "north", "password": PASSWORD, "system_id": SYSTEM},
        ).status_code
        == 401
    )
    assert client.get("/api/v1/auth/me", headers=headers(current)).status_code == 200


def test_authentication_rejects_missing_forged_expired_and_malformed_claims(auth_env):
    client, _, _, _, _ = auth_env
    for path in (
        "/api/v1/systems",
        PREFIX + "/stations",
        PREFIX + "/stream",
        "/api/v1/admin/users",
    ):
        response = client.get(path)
        assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
    assert client.get("/livez").status_code == 200
    pair = login(client, "north")
    key = get_settings().auth_secret.get_secret_value()
    claims = jwt.decode(pair["access_token"], key, algorithms=["HS256"], audience="gbfs-dashboard")
    variants = [
        {**claims, "exp": int(time.time()) - 1},
        {**claims, "role": "superuser"},
        {**claims, "system_id": None},
        {**claims, "regions": "r1"},
        {key: value for key, value in claims.items() if key != "regions"},
        {**claims, "aud": "other"},
    ]
    for changed in variants:
        token = jwt.encode(changed, key, algorithm="HS256")
        assert (
            client.get(
                PREFIX + "/stations", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )
    forged = jwt.encode({**claims, "role": "admin"}, secrets.token_urlsafe(48), algorithm="HS256")
    assert (
        client.get(PREFIX + "/stations", headers={"Authorization": f"Bearer {forged}"}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "missing", "password": PASSWORD}
        ).json()["detail"]
        == "invalid_credentials"
    )
    for name in ("north", "unscoped"):
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": name, "password": PASSWORD}
            ).status_code
            == 403
        )


def test_roles_zones_and_warm_cache_cannot_leak(auth_env):
    client, _, _, _, now = auth_env
    north, south, admin, manager, empty = (
        login(client, name) for name in ("north", "south", "admin", "manager", "empty")
    )
    # Сначала прогреваем наиболее широкий кэш, затем по очереди две непересекающиеся зоны.
    for pair, expected in (
        (admin, ["s1", "s2", "s3"]),
        (north, ["s1"]),
        (south, ["s2"]),
        (north, ["s1"]),
    ):
        result = client.get(PREFIX + "/stations", headers=headers(pair))
        assert result.status_code == 200, result.text
        assert [s["station_id"] for s in result.json()["stations"]] == expected
    assert [s["id"] for s in client.get("/api/v1/systems", headers=headers(north)).json()] == [
        SYSTEM
    ]
    assert client.get("/api/v1/systems/other/stations", headers=headers(north)).status_code == 403
    assert (
        client.get(PREFIX + "/stations?region_id=r2", headers=headers(north)).json()["stations"]
        == []
    )
    for suffix in ("", "/history", "/events", "/rollups"):
        assert (
            client.get(
                PREFIX + "/stations/s2" + suffix, headers=headers(north), params=params(now)
            ).status_code
            == 404
        )
    for suffix in (
        "/stations",
        "/stations/s1",
        "/stations/s1/history",
        "/stations/s1/events",
        "/stream",
    ):
        assert (
            client.get(PREFIX + suffix, headers=headers(manager), params=params(now)).status_code
            == 403
        )
    catalog = client.get(PREFIX + "/catalog", headers=headers(manager))
    assert catalog.status_code == 200
    assert [station["station_id"] for station in catalog.json()["items"]] == ["s1"]
    assert "status" not in catalog.json()["items"][0]
    result = client.get(
        PREFIX + "/stations/s1/rollups",
        headers=headers(manager),
        params={**params(now), "resolution": "hour"},
    )
    assert result.status_code == 200 and len(result.json()["items"]) == 1
    assert (
        client.get(PREFIX + "/stations", headers=headers(empty)).json()["detail"] == "scope_empty"
    )
    assert client.get("/api/v1/admin/users", headers=headers(north)).status_code == 403
    assert (
        client.get(
            PREFIX + "/stations/s1/history",
            headers=headers(north),
            params={
                "start": (now - timedelta(days=2)).isoformat(),
                "end": now.isoformat(),
            },
        ).status_code
        == 422
    )


def test_admin_user_zone_scope_management_and_audit(auth_env):
    client, database, users, zones, _ = auth_env
    admin = headers(login(client, "admin"))
    north = login(client, "north")
    result = client.post(
        "/api/v1/admin/users",
        headers=admin,
        json={"username": "new-user", "password": PASSWORD, "role": "operator"},
    )
    assert result.status_code == 201
    new_id = result.json()["id"]
    assert "password_hash" not in result.json()
    assert (
        client.post(
            "/api/v1/admin/users",
            headers=admin,
            json={"username": "new-user", "password": PASSWORD, "role": "operator"},
        ).status_code
        == 409
    )
    default = client.get("/api/v1/admin/zones", headers=admin, params={"system_id": SYSTEM})
    whole = next(z for z in default.json() if z["name"] == "Вся система")
    assert whole["region_ids"] is None
    assert (
        client.put(
            f"/api/v1/admin/users/{new_id}/scopes/{SYSTEM}",
            headers=admin,
            json={"zone_id": whole["id"]},
        ).status_code
        == 200
    )
    whole_pair = login(client, "new-user")
    assert (
        len(client.get(PREFIX + "/stations", headers=headers(whole_pair)).json()["stations"]) == 3
    )
    assert (
        client.put(
            f"/api/v1/admin/users/{new_id}/scopes/other",
            headers=admin,
            json={"zone_id": whole["id"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/admin/zones",
            headers=admin,
            json={"system_id": SYSTEM, "name": "bad", "region_ids": ["foreign"]},
        ).status_code
        == 422
    )
    assert client.delete(f"/api/v1/admin/zones/{zones['north']}", headers=admin).status_code == 409
    assert (
        client.put(
            f"/api/v1/admin/zones/{zones['north']}",
            headers=admin,
            json={"system_id": SYSTEM, "name": "north", "region_ids": ["r2"]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": north["refresh_token"]}
        ).status_code
        == 401
    )
    assert (
        client.delete(f"/api/v1/admin/users/{new_id}/scopes/{SYSTEM}", headers=admin).status_code
        == 204
    )
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": whole_pair["refresh_token"]}
        ).status_code
        == 401
    )
    assert (
        client.patch(
            f"/api/v1/admin/users/{users['admin']}", headers=admin, json={"active": False}
        ).status_code
        == 409
    )
    assert (
        client.patch(
            f"/api/v1/admin/users/{users['north']}", headers=admin, json={"active": False}
        ).status_code
        == 200
    )
    audit = client.get("/api/v1/admin/audit", headers=admin)
    assert audit.status_code == 200
    assert {"user.create", "scope.put", "scope.delete", "zone.update", "user.update"} <= {
        item["action"] for item in audit.json()
    }
    assert PASSWORD not in audit.text
    with database.session() as session:
        assert session.scalar(select(AuditLog.id).limit(1)) is not None


def test_rls_fail_closed_role_grants_and_pool_reset(auth_env):
    _, _, _, _, _ = auth_env
    database = Database(get_settings(), pool_size=1, max_overflow=0)
    try:
        with database.session() as session:
            session.execute(text("set local role app_operator"))
            assert session.scalars(select(Station)).all() == []
            assert session.scalars(select(StationCurrent)).all() == []
        with database.session() as session:
            Access("operator", SYSTEM, ("r1",)).apply(session)
            assert list(session.scalars(select(Station.station_id))) == ["s1"]
            assert list(session.scalars(select(StationCurrent.station_id))) == ["s1"]
            assert list(session.scalars(select(StationRollup.station_id))) == ["s1"]
        with database.session() as session:
            assert session.scalar(text("select current_user")) == "app_api"
            session.execute(text("set local role app_operator"))
            assert session.scalars(select(StationCurrent)).all() == []
        for table in ("core.station_current", "core.station_status", "core.station_status_default"):
            with pytest.raises(ProgrammingError), database.session() as session:
                Access("manager", SYSTEM, None).apply(session)
                session.execute(text(f"select * from {table}"))  # noqa: S608 — статический список
        with pytest.raises(ProgrammingError), database.session() as session:
            session.execute(select(StationCurrent))
        with database.session() as session:
            Access("manager", SYSTEM, ("r2",)).apply(session)
            assert list(session.scalars(select(StationRollup.station_id))) == ["s2"]
    finally:
        database.dispose()


def test_sse_snapshot_delta_removals_and_expiry_are_scoped(auth_env):
    client, database, _, _, now = auth_env

    async def check_hub():
        reads = ReadService(Database(get_settings()), get_settings())
        hub = StreamService(reads, get_settings())
        north, south = Access("operator", SYSTEM, ("r1",)), Access("operator", SYSTEM, ("r2",))
        qn, qs = hub.subscribe(SYSTEM, north), hub.subscribe(SYSTEM, south)
        try:
            sn, ss = await reads.snapshot(SYSTEM, north), await reads.snapshot(SYSTEM, south)
            assert '"s2"' not in hub.initial_message(sn, north).decode()
            assert '"s1"' not in hub.initial_message(ss, south).decode()
            write(database, [row(now + timedelta(seconds=1), 0)], now + timedelta(seconds=1))
            reads.invalidate(SYSTEM)
            await hub.publish((SYSTEM, north))
            await hub.publish((SYSTEM, south))
            assert '"s1"' in qn.get_nowait() and qs.empty()
            with database.transaction() as session:
                station = session.scalar(select(Station).where(Station.station_id == "s1"))
                station.region_id = "r2"
            reads.invalidate(SYSTEM)
            await hub.publish((SYSTEM, north))
            await hub.publish((SYSTEM, south))
            message = qn.get_nowait()
            data = json.loads(message.split("data: ", 1)[1])
            assert data["removed"] == ["s1"] and data["stations"] == []
            assert '"s1"' in qs.get_nowait()
        finally:
            hub.unsubscribe(SYSTEM, qn, north)
            hub.unsubscribe(SYSTEM, qs, south)
            await reads.close()
            reads.database.dispose()
        assert hub.count == 0 and not hub.initial_messages

    asyncio.run(check_hub())
    pair = login(client, "south")
    key = get_settings().auth_secret.get_secret_value()
    claims = jwt.decode(pair["access_token"], key, algorithms=["HS256"], audience="gbfs-dashboard")
    claims["exp"] = int(time.time()) + 2
    short = {"access_token": jwt.encode(claims, key, algorithm="HS256")}
    with client.stream("GET", PREFIX + "/stream", headers=headers(short)) as response:
        body = response.read().decode()
    assert response.status_code == 200
    assert "event: snapshot" in body and "event: auth_expired" in body
    assert client.app.state.stream.count == 0


def test_login_rate_limit_and_missing_secret(auth_env, monkeypatch):
    client, _, _, _, _ = auth_env
    monkeypatch.setenv("AUTH_SECRET", "")
    get_settings.cache_clear()
    result = client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    assert result.status_code == 503 and result.json()["detail"] == "auth_not_configured"
    for _ in range(9):
        assert client.post("/api/v1/auth/login", json={}).status_code == 422
    result = client.post("/api/v1/auth/login", json={})
    assert result.status_code == 429 and result.headers["retry-after"] == "60"


def test_concurrent_refresh_and_password_role_changes(auth_env):
    from concurrent.futures import ThreadPoolExecutor

    client, _, users, _, _ = auth_env
    pair = login(client, "north")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: client.post(
                    "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
                ),
                range(2),
            )
        )
    assert sorted(result.status_code for result in results) == [200, 401]
    current = next(result.json() for result in results if result.status_code == 200)
    admin = headers(login(client, "admin"))
    new_password = secrets.token_urlsafe(24)
    result = client.patch(
        f"/api/v1/admin/users/{users['north']}",
        headers=admin,
        json={"password": new_password, "role": "manager"},
    )
    assert result.status_code == 200
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": current["refresh_token"]}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "north", "password": PASSWORD, "system_id": SYSTEM},
        ).status_code
        == 401
    )
    result = client.post(
        "/api/v1/auth/login",
        json={"username": "north", "password": new_password, "system_id": SYSTEM},
    )
    assert result.status_code == 200
    assert client.get(PREFIX + "/stations", headers=headers(result.json())).status_code == 403


def test_admin_commit_conflict_is_http_409(auth_env):
    client, _, _, zones, _ = auth_env
    admin = headers(login(client, "admin"))
    response = client.put(
        f"/api/v1/admin/zones/{zones['north']}",
        headers=admin,
        json={"system_id": SYSTEM, "name": "south", "region_ids": ["r1"]},
    )
    assert response.status_code == 409
    listed = client.get("/api/v1/admin/zones", headers=admin, params={"system_id": SYSTEM}).json()
    assert next(z for z in listed if z["id"] == zones["north"])["name"] == "north"
