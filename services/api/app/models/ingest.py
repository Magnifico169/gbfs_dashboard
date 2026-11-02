"""Журнал приёма и настройки систем (схема app)."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import IngestStatus
from app.models.base import TIMESTAMPTZ, Base

INGEST_STATUSES = ", ".join(f"'{status.value}'" for status in IngestStatus)


class SystemSetting(Base):
    """Настройки систем: пишет админка, приём только читает."""

    __tablename__ = "system_setting"
    __table_args__ = (
        CheckConstraint("poll_interval_s > 0", name="system_setting_poll_interval_s_check"),
        {"schema": "app"},
    )

    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    discovery_url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # None — брать ttl из фида
    poll_interval_s: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())


class IngestRun(Base):
    __tablename__ = "ingest_run"
    __table_args__ = (
        CheckConstraint(f"status in ({INGEST_STATUSES})", name="ingest_run_status"),
        Index("ix_ingest_run_system_feed_started", "system_id", "feed", text("started_at desc")),
        {"schema": "app"},
    )

    run_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    system_id: Mapped[str] = mapped_column(Text)
    feed: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    finished_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    status: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    rows_in: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_written: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_quarantined: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
