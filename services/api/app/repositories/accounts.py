"""Управление учётными записями; транзакцией управляет вызывающий код."""

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Region, System
from app.models.auth import AuditLog, UserAccount, UserScope, Zone
from app.schemas.auth import UserCreate, UserUpdate, ZoneCreate
from app.services.auth import passwords, revoke_sessions


def user_data(user: UserAccount):
    return {"id": user.id, "username": user.username, "role": user.role_id, "active": user.active}


def zone_data(zone: Zone):
    return {
        "id": zone.id,
        "system_id": zone.system_id,
        "name": zone.name,
        "region_ids": zone.region_ids,
    }


def audit(session, actor, action, target):
    session.add(AuditLog(actor_id=actor, action=action, target=str(target)))


class AccountRepository:
    def __init__(self, session: Session, actor: int | None):
        self.session, self.actor = session, actor

    def user(self, user_id: int):
        user = self.session.scalar(
            select(UserAccount).where(UserAccount.id == user_id).with_for_update()
        )
        if user is None:
            raise HTTPException(404, "user_not_found")
        return user

    def create_user(self, body: UserCreate):
        user = UserAccount(
            username=body.username,
            password_hash=passwords.hash(body.password.get_secret_value()),
            role_id=body.role,
            active=True,
        )
        self.session.add(user)
        self.session.flush()
        audit(self.session, self.actor, "user.create", user.id)
        return user_data(user)

    def update_user(self, user_id: int, body: UserUpdate):
        user = self.user(user_id)
        if user_id == self.actor and (body.active is False or body.role not in (None, "admin")):
            raise HTTPException(409, "cannot_remove_own_admin_access")
        if body.role is not None:
            user.role_id = body.role
        if body.active is not None:
            user.active = body.active
        if body.password is not None:
            user.password_hash = passwords.hash(body.password.get_secret_value())
        revoke_sessions(self.session, user_id)
        audit(self.session, self.actor, "user.update", user_id)
        return user_data(user)

    def default_zone(self, system_id: str):
        if self.session.get(System, system_id) is None:
            raise HTTPException(404, "system_not_found")
        self.session.execute(
            insert(Zone)
            .values(
                system_id=system_id,
                name="Вся система",
                region_ids=None,
            )
            .on_conflict_do_nothing(index_elements=["system_id", "name"])
        )

    def validate_zone(self, body: ZoneCreate):
        if body.name == "Вся система":
            raise HTTPException(409, "default_zone_name_reserved")
        if self.session.get(System, body.system_id) is None:
            raise HTTPException(404, "system_not_found")
        if body.region_ids is not None:
            known = set(
                self.session.scalars(
                    select(Region.id).where(
                        Region.system_id == body.system_id, Region.id.in_(body.region_ids)
                    )
                )
            )
            if known != set(body.region_ids):
                raise HTTPException(422, "unknown_region")

    def create_zone(self, body: ZoneCreate):
        self.validate_zone(body)
        zone = Zone(
            system_id=body.system_id,
            name=body.name,
            region_ids=sorted(set(body.region_ids)) if body.region_ids is not None else None,
        )
        self.session.add(zone)
        self.session.flush()
        audit(self.session, self.actor, "zone.create", zone.id)
        return zone_data(zone)

    def update_zone(self, zone_id: int, body: ZoneCreate):
        zone = self.session.get(Zone, zone_id)
        if zone is None:
            raise HTTPException(404, "zone_not_found")
        if zone.system_id != body.system_id or zone.name == "Вся система":
            raise HTTPException(409, "zone_system_or_default_immutable")
        self.validate_zone(body)
        zone.name = body.name
        zone.region_ids = sorted(set(body.region_ids)) if body.region_ids is not None else None
        for user_id in self.session.scalars(
            select(UserScope.user_id).where(UserScope.zone_id == zone_id)
        ):
            revoke_sessions(self.session, user_id)
        audit(self.session, self.actor, "zone.update", zone_id)
        return zone_data(zone)

    def delete_zone(self, zone_id: int):
        zone = self.session.get(Zone, zone_id)
        if zone is None:
            raise HTTPException(404, "zone_not_found")
        if zone.name == "Вся система":
            raise HTTPException(409, "default_zone_immutable")
        if self.session.scalar(
            select(UserScope.user_id).where(UserScope.zone_id == zone_id).limit(1)
        ):
            raise HTTPException(409, "zone_in_use")
        self.session.delete(zone)
        audit(self.session, self.actor, "zone.delete", zone_id)

    def put_scope(self, user_id: int, system_id: str, zone_id: int):
        self.user(user_id)
        zone = self.session.get(Zone, zone_id)
        if zone is None or zone.system_id != system_id:
            raise HTTPException(422, "zone_system_mismatch")
        scope = self.session.get(UserScope, (user_id, system_id))
        if scope is None:
            self.session.add(UserScope(user_id=user_id, system_id=system_id, zone_id=zone_id))
        else:
            scope.zone_id = zone_id
        revoke_sessions(self.session, user_id)
        audit(self.session, self.actor, "scope.put", f"{user_id}:{system_id}")
        return {"user_id": user_id, "system_id": system_id, "zone_id": zone_id}

    def delete_scope(self, user_id: int, system_id: str):
        self.user(user_id)
        self.session.execute(
            delete(UserScope).where(UserScope.user_id == user_id, UserScope.system_id == system_id)
        )
        revoke_sessions(self.session, user_id)
        audit(self.session, self.actor, "scope.delete", f"{user_id}:{system_id}")
