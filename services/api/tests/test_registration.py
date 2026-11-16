import concurrent.futures

from sqlalchemy import func, select
from test_m3 import PASSWORD, SYSTEM, headers, login
from test_m3 import auth_env as auth_env

from app.core.config.settings import get_settings
from app.models.auth import AuditLog, AuthSession, UserAccount, UserScope, Zone
from app.schemas.auth import Register
from app.services.auth import passwords
from app.services.registration import register


def body(username="new-operator", **changes):
    return {"username": username, "password": PASSWORD, "system_id": SYSTEM, **changes}


def test_public_registration_creates_operator_and_one_scope(auth_env):
    client, database, _, _, _ = auth_env
    systems = client.get("/api/v1/auth/systems")
    assert systems.status_code == 200
    assert all(set(row) == {"id", "name"} for row in systems.json())
    assert client.get("/api/v1/systems").status_code == 401
    result = client.post("/api/v1/auth/register", json=body())
    assert result.status_code == 201, result.text
    assert result.headers["cache-control"] == "no-store"
    me = client.get("/api/v1/auth/me", headers=headers(result.json())).json()
    assert me["role"] == "operator"
    assert me["system_id"] == SYSTEM
    assert me["region_ids"] is None
    assert client.get("/api/v1/admin/users", headers=headers(result.json())).status_code == 403
    assert (
        client.get("/api/v1/systems/other/stations", headers=headers(result.json())).status_code
        == 403
    )
    with database.session() as session:
        user = session.scalar(select(UserAccount).where(UserAccount.username == "new-operator"))
        assert passwords.verify(PASSWORD, user.password_hash)
        assert user.password_hash != PASSWORD
        scope = session.get(UserScope, (user.id, SYSTEM))
        assert session.get(Zone, scope.zone_id).name == "Вся система"
        assert (
            session.scalar(select(AuditLog.action).where(AuditLog.actor_id == user.id))
            == "auth.register"
        )
        assert (
            session.scalar(
                select(func.count()).select_from(AuthSession).where(AuthSession.user_id == user.id)
            )
            == 1
        )
    # Назначение менеджера остаётся административной операцией.
    admin = headers(login(client, "admin"))
    assert (
        client.patch(
            f"/api/v1/admin/users/{me['id']}", json={"role": "manager"}, headers=admin
        ).status_code
        == 200
    )
    pair = client.post("/api/v1/auth/login", json=body()).json()
    assert client.get("/api/v1/auth/me", headers=headers(pair)).json()["role"] == "manager"


def test_registration_rejects_privileged_fields_and_duplicates(auth_env):
    client, database, _, _, _ = auth_env
    for extra in ({"role": "admin"}, {"role": "manager"}, {"region_ids": None}, {"active": True}):
        response = client.post("/api/v1/auth/register", json=body(**extra))
        assert response.status_code == 422
        assert PASSWORD not in response.text
    invalid = client.post("/api/v1/auth/register", json=body(password=PASSWORD[:5]))
    assert invalid.status_code == 422
    assert PASSWORD[:5] not in invalid.text
    assert client.post("/api/v1/auth/register", json=body(system_id="missing")).status_code == 404
    assert client.post("/api/v1/auth/register", json=body()).status_code == 201
    assert client.post("/api/v1/auth/register", json=body()).status_code == 409
    with database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserAccount)
                .where(UserAccount.username == "new-operator")
            )
            == 1
        )
        assert session.scalar(select(Zone).where(Zone.system_id == "missing")) is None


def test_browser_registration_confirmation_errors_and_auto_login(auth_env):
    client, database, _, _, _ = auth_env
    assert "/register" in client.get("/login").text
    page = client.get("/register")
    assert page.status_code == 200
    assert 'autocomplete="new-password"' in page.text
    data = {**body(), "password_confirm": "different"}
    failed = client.post("/register", data=data)
    assert failed.status_code == 422
    assert "Пароли не совпадают" in failed.text
    assert PASSWORD not in failed.text
    assert (
        client.post("/register", data=data, headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    with database.session() as session:
        assert (
            session.scalar(select(UserAccount).where(UserAccount.username == "new-operator"))
            is None
        )
    data["password_confirm"] = PASSWORD
    result = client.post("/register", data={**data, "role": "admin"}, follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"] == "/ui/operator"
    assert len(result.headers.get_list("set-cookie")) == 2
    assert all(
        "HttpOnly" in value and "SameSite=strict" in value
        for value in result.headers.get_list("set-cookie")
    )
    assert client.get("/ui/operator").status_code == 200
    assert client.get("/ui/manager").status_code == 403
    duplicate = client.post("/register", data=data)
    assert duplicate.status_code == 409
    assert "Этот логин уже занят" in duplicate.text
    assert PASSWORD not in duplicate.text


def test_concurrent_registrations_create_one_user(auth_env):
    _, database, _, _, _ = auth_env

    def attempt():
        from fastapi import HTTPException

        try:
            with database.transaction() as session:
                register(session, Register(**body()), get_settings())
            return 201
        except HTTPException as exc:
            return exc.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == [201, 409]


def test_registration_shares_login_rate_limit(auth_env):
    client, _, _, _, _ = auth_env
    for _ in range(10):
        assert client.post("/api/v1/auth/register", json={}).status_code == 422
    blocked = client.post("/register", data={})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"
    assert "Подождите минуту" in blocked.text
    assert client.post("/api/v1/auth/login", json={}).status_code == 429
