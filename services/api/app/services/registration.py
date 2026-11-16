"""Самостоятельная регистрация: только оператор и одна выбранная система."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config.settings import Settings
from app.models import System
from app.models.auth import AuditLog, UserAccount, UserScope, Zone
from app.repositories.accounts import AccountRepository
from app.schemas.auth import Register, TokenPair
from app.services.access import Access
from app.services.auth import issue_tokens, passwords


def systems(session: Session) -> list[dict]:
    # Публичен только справочник названий систем; данные станций требуют входа.
    Access("admin", None, None).apply(session)
    return [
        dict(row)
        for row in session.execute(
            select(System.id, System.name).order_by(System.name, System.id)
        ).mappings()
    ]


def register(session: Session, body: Register, settings: Settings) -> TokenPair:
    # Сервисный контекст нужен для справочника и назначения зоны. Роль создаваемого
    # пользователя фиксирована сервером; публичный запрос не задаёт роль или region_ids.
    Access("admin", None, None).apply(session)
    repository = AccountRepository(session, None)
    repository.default_zone(body.system_id)
    zone = session.scalar(
        select(Zone).where(Zone.system_id == body.system_id, Zone.name == "Вся система")
    )
    user = session.scalar(
        insert(UserAccount)
        .values(
            username=body.username,
            password_hash=passwords.hash(body.password.get_secret_value()),
            role_id="operator",
            active=True,
        )
        .on_conflict_do_nothing(index_elements=["username"])
        .returning(UserAccount)
    )
    if user is None:
        raise HTTPException(409, "username_taken")
    session.add(UserScope(user_id=user.id, system_id=body.system_id, zone_id=zone.id))
    session.add(AuditLog(actor_id=user.id, action="auth.register", target=str(user.id)))
    session.flush()
    return issue_tokens(session, user, body.system_id, settings)
