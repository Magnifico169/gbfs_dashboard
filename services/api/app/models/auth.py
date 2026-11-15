"""Учётные записи, зоны и сеансы (писатель — Python API)."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Entity


class Role(Base):
    __tablename__ = "role"
    __table_args__ = {"schema": "app"}

    id: Mapped[str] = mapped_column(Text, primary_key=True)


class UserAccount(Entity):
    __tablename__ = "user_account"
    __table_args__ = {"schema": "app"}

    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role_id: Mapped[str] = mapped_column(ForeignKey("app.role.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Zone(Entity):
    __tablename__ = "zone"
    __table_args__ = (
        UniqueConstraint("id", "system_id"),
        UniqueConstraint("system_id", "name"),
        {"schema": "app"},
    )

    system_id: Mapped[str] = mapped_column(ForeignKey("core.system.id"))
    name: Mapped[str] = mapped_column(Text)
    # null — вся система; [] — ни одной станции (fail closed).
    region_ids: Mapped[list[str] | None] = mapped_column(JSONB)


class UserScope(Base):
    __tablename__ = "user_scope"
    __table_args__ = (
        ForeignKeyConstraint(["zone_id", "system_id"], ["app.zone.id", "app.zone.system_id"]),
        {"schema": "app"},
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("app.user_account.id"), primary_key=True)
    system_id: Mapped[str] = mapped_column(ForeignKey("core.system.id"), primary_key=True)
    zone_id: Mapped[int] = mapped_column(BigInteger)


class AuthSession(Base):
    __tablename__ = "auth_session"
    __table_args__ = {"schema": "app"}

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app.user_account.id"), index=True)
    system_id: Mapped[str | None] = mapped_column(ForeignKey("core.system.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Entity):
    __tablename__ = "audit_log"
    __table_args__ = {"schema": "app"}

    actor_id: Mapped[int | None] = mapped_column(ForeignKey("app.user_account.id"))
    action: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
