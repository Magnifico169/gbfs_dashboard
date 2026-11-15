"""Короткий JWT проверяется локально; обновление перечитывает права из БД."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from fastapi import HTTPException
from pwdlib import PasswordHash
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config.settings import Settings
from app.models.auth import AuditLog, AuthSession, UserAccount, UserScope, Zone
from app.schemas.auth import RoleName, TokenPair
from app.services.access import Access

passwords = PasswordHash.recommended()


@lru_cache
def dummy_hash() -> str:
    return passwords.hash(secrets.token_urlsafe(32))


def invalid_credentials() -> HTTPException:
    return HTTPException(401, "invalid_credentials", headers={"WWW-Authenticate": "Bearer"})


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sub: str
    role: RoleName
    system_id: str | None
    regions: list[str] | None
    exp: int
    iat: int
    iss: str
    aud: str

    @property
    def access(self) -> Access:
        return Access(
            self.role, self.system_id, tuple(self.regions) if self.regions is not None else None
        )


def signing_key(settings: Settings) -> str:
    key = settings.auth_secret.get_secret_value()
    if len(key.encode()) < 32:
        raise HTTPException(503, "auth_not_configured")
    return key


def decode_token(token: str, settings: Settings) -> Principal:
    try:
        claims = jwt.decode(
            token,
            signing_key(settings),
            algorithms=["HS256"],
            issuer="gbfs-api",
            audience="gbfs-dashboard",
            options={"require": ["sub", "role", "exp", "iat", "iss", "aud"]},
        )
        principal = Principal.model_validate(claims)
        if not principal.sub.isdecimal():
            raise ValueError("invalid subject")
        if principal.role != "admin" and not principal.system_id:
            raise ValueError("missing scope")
        return principal
    except (jwt.InvalidTokenError, ValidationError, ValueError) as exc:
        raise invalid_credentials() from exc


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_tokens(
    session: Session, user: UserAccount, system_id: str | None, settings: Settings
) -> TokenPair:
    regions = None
    if user.role_id != "admin":
        scope = session.get(UserScope, (user.id, system_id)) if system_id else None
        if scope is None:
            raise HTTPException(403, "scope_required")
        zone = session.get(Zone, scope.zone_id)
        regions = zone.region_ids
    now = datetime.now(UTC)
    claims = Principal(
        sub=str(user.id),
        role=user.role_id,
        system_id=system_id,
        regions=regions,
        iat=int(now.timestamp()),
        exp=int(now.timestamp()) + settings.auth_access_ttl_s,
        iss="gbfs-api",
        aud="gbfs-dashboard",
    )
    access_token = jwt.encode(claims.model_dump(), signing_key(settings), algorithm="HS256")
    session.execute(
        delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.expires_at <= now)
    )
    refresh = secrets.token_urlsafe(48)
    session.add(
        AuthSession(
            token_hash=token_hash(refresh),
            user_id=user.id,
            system_id=system_id,
            expires_at=now + timedelta(seconds=settings.auth_refresh_ttl_s),
        )
    )
    return TokenPair(
        access_token=access_token, refresh_token=refresh, expires_in=settings.auth_access_ttl_s
    )


def login(
    session: Session, username: str, password: str, system_id: str | None, settings: Settings
) -> TokenPair:
    user = session.scalar(
        select(UserAccount).where(UserAccount.username == username).with_for_update()
    )
    valid = passwords.verify(password, user.password_hash if user else dummy_hash())
    if user is None or not valid or not user.active:
        raise invalid_credentials()
    pair = issue_tokens(session, user, system_id, settings)
    session.add(AuditLog(actor_id=user.id, action="login", target=str(user.id)))
    return pair


def refresh(session: Session, token: str, settings: Settings) -> TokenPair:
    stored = session.get(AuthSession, token_hash(token))
    if stored is None:
        raise invalid_credentials()
    user = session.scalar(
        select(UserAccount).where(UserAccount.id == stored.user_id).with_for_update()
    )
    stored = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash(token)).with_for_update()
    )
    if stored is None or stored.expires_at <= datetime.now(UTC):
        raise invalid_credentials()
    if not user.active:
        raise invalid_credentials()
    pair = issue_tokens(session, user, stored.system_id, settings)
    session.delete(stored)
    return pair


def logout(session: Session, token: str) -> None:
    stored = session.get(AuthSession, token_hash(token))
    if stored:
        session.add(AuditLog(actor_id=stored.user_id, action="logout", target=str(stored.user_id)))
        session.delete(stored)


def revoke_sessions(session: Session, user_id: int) -> None:
    session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
