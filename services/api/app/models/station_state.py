"""Состояние станций (схема core): текущее, факты, события, карантин."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Identity, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TIMESTAMPTZ, Base


class StationStateMixin:
    """Состояние станции. reported_at = None — станция не отчитывалась (фид отдаёт заглушку
    86400): это «неизвестно», а не «0 доступно». Необязательные в GBFS счётчики могут быть None.
    """

    reported_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    bikes_available: Mapped[int] = mapped_column(Integer)
    ebikes_available: Mapped[int | None] = mapped_column(Integer)
    bikes_disabled: Mapped[int | None] = mapped_column(Integer)
    docks_available: Mapped[int | None] = mapped_column(Integer)
    docks_disabled: Mapped[int | None] = mapped_column(Integer)
    is_installed: Mapped[bool] = mapped_column(Boolean)
    is_renting: Mapped[bool] = mapped_column(Boolean)
    is_returning: Mapped[bool] = mapped_column(Boolean)


STATE_COLUMNS = (
    "bikes_available",
    "ebikes_available",
    "bikes_disabled",
    "docks_available",
    "docks_disabled",
    "is_installed",
    "is_renting",
    "is_returning",
)


class StationCurrent(StationStateMixin, Base):
    __tablename__ = "station_current"
    __table_args__ = {"schema": "core"}

    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    station_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)  # когда состояние изменилось
    checked_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)  # последняя сверка с фидом


class StationStatus(StationStateMixin, Base):
    """Факты: только изменившиеся состояния. Партиции — суточные (см. ревизию 0001)."""

    __tablename__ = "station_status"
    __table_args__ = {"schema": "core", "postgresql_partition_by": "RANGE (fetched_at)"}

    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    station_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, primary_key=True)


class StationEvent(Base):
    """Создаётся в M1, наполняется с M2 (вывод событий из дельт)."""

    __tablename__ = "station_event"
    __table_args__ = (
        Index("ix_station_event_station_time", "system_id", "station_id", text("occurred_at desc")),
        {"schema": "core"},
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    system_id: Mapped[str] = mapped_column(Text)
    station_id: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    event_type: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)


class StationQuarantine(Base):
    __tablename__ = "station_quarantine"
    __table_args__ = (
        Index("ix_station_quarantine_system_time", "system_id", text("fetched_at desc")),
        {"schema": "core"},
    )

    quarantine_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    system_id: Mapped[str] = mapped_column(Text)
    station_id: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
