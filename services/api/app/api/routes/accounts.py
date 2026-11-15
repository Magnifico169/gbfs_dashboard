from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import Admin
from app.api.deps import get_session
from app.models import Region, System
from app.models.auth import AuditLog, UserAccount, UserScope, Zone
from app.repositories.accounts import AccountRepository, user_data, zone_data
from app.repositories.dashboard import serialize
from app.schemas.auth import ScopePut, UserCreate, UserUpdate, ZoneCreate
from app.services.access import Access

router = APIRouter(prefix="/api/v1/admin", tags=["administration"])
SessionDep = Annotated[Session, Depends(get_session, scope="function")]
Limit = Annotated[int, Query(ge=1, le=500)]


@router.get("/users")
def users(admin: Admin, session: SessionDep, after: int = 0, limit: Limit = 100):
    return [
        user_data(user)
        for user in session.scalars(
            select(UserAccount).where(UserAccount.id > after).order_by(UserAccount.id).limit(limit)
        )
    ]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, admin: Admin, session: SessionDep):
    return AccountRepository(session, int(admin.sub)).create_user(body)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, admin: Admin, session: SessionDep):
    return AccountRepository(session, int(admin.sub)).update_user(user_id, body)


@router.get("/zones")
def zones(admin: Admin, session: SessionDep, system_id: str, after: int = 0, limit: Limit = 100):
    Access("admin", None, None).apply(session)
    AccountRepository(session, int(admin.sub)).default_zone(system_id)
    return [
        zone_data(zone)
        for zone in session.scalars(
            select(Zone)
            .where(Zone.system_id == system_id, Zone.id > after)
            .order_by(Zone.id)
            .limit(limit)
        )
    ]


@router.post("/zones", status_code=201)
def create_zone(body: ZoneCreate, admin: Admin, session: SessionDep):
    # Справочник защищён RLS; административный контекст задаётся только после require_admin.
    Access("admin", None, None).apply(session)
    result = AccountRepository(session, int(admin.sub)).create_zone(body)
    return result


@router.put("/zones/{zone_id}")
def update_zone(zone_id: int, body: ZoneCreate, admin: Admin, session: SessionDep):
    Access("admin", None, None).apply(session)
    return AccountRepository(session, int(admin.sub)).update_zone(zone_id, body)


@router.delete("/zones/{zone_id}", status_code=204)
def delete_zone(zone_id: int, admin: Admin, session: SessionDep):
    return AccountRepository(session, int(admin.sub)).delete_zone(zone_id)


@router.get("/systems")
def systems(admin: Admin, session: SessionDep, after: str = "", limit: Limit = 100):
    Access("admin", None, None).apply(session)
    return [
        serialize(row)
        for row in session.scalars(
            select(System).where(System.id > after).order_by(System.id).limit(limit)
        )
    ]


@router.get("/systems/{system_id}/regions")
def regions(system_id: str, admin: Admin, session: SessionDep, after: str = "", limit: Limit = 100):
    Access("admin", None, None).apply(session)
    return [
        serialize(row)
        for row in session.scalars(
            select(Region)
            .where(Region.system_id == system_id, Region.id > after)
            .order_by(Region.id)
            .limit(limit)
        )
    ]


@router.get("/users/{user_id}/scopes")
def scopes(user_id: int, admin: Admin, session: SessionDep):
    AccountRepository(session, int(admin.sub)).user(user_id)
    return [
        {"system_id": row.system_id, "zone_id": row.zone_id}
        for row in session.scalars(
            select(UserScope).where(UserScope.user_id == user_id).order_by(UserScope.system_id)
        )
    ]


@router.put("/users/{user_id}/scopes/{system_id}")
def put_scope(user_id: int, system_id: str, body: ScopePut, admin: Admin, session: SessionDep):
    return AccountRepository(session, int(admin.sub)).put_scope(user_id, system_id, body.zone_id)


@router.delete("/users/{user_id}/scopes/{system_id}", status_code=204)
def delete_scope(user_id: int, system_id: str, admin: Admin, session: SessionDep):
    AccountRepository(session, int(admin.sub)).delete_scope(user_id, system_id)


@router.get("/audit")
def audit(admin: Admin, session: SessionDep, before: int | None = None, limit: Limit = 100):
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if before is not None:
        query = query.where(AuditLog.id < before)
    return [serialize(row) for row in session.scalars(query)]
