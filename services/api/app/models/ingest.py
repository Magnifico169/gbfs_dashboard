"""Журнал приёма и настройки систем (схема app)."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import IngestStatus
from app.models.base import Base, Entity

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
    # None — использовать POLL_TIMEOUT_S; успешный опрос учитывает TTL фида
    poll_interval_s: Mapped[int | None] = mapped_column(Integer)


class IngestRun(Entity):
    __tablename__ = "ingest_run"
    __table_args__ = (
        CheckConstraint(f"status in ({INGEST_STATUSES})", name="ingest_run_status"),
        Index("ix_ingest_run_system_feed_started", "system_id", "feed", text("started_at desc")),
        {"schema": "app"},
    )

    system_id: Mapped[str] = mapped_column(Text)
    feed: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    rows_in: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_written: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_quarantined: Mapped[int] = mapped_column(Integer, server_default=text("0"))
