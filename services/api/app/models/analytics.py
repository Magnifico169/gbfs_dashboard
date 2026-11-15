"""Ролл-апы: единственный писатель — сервис аналитики на R."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Double, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StationRollup(Base):
    __tablename__ = "station_rollup"
    __table_args__ = (
        CheckConstraint("resolution in ('10m', 'hour', 'day')", name="rollup_resolution"),
        {"schema": "mart"},
    )

    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    station_id: Mapped[str] = mapped_column(Text, primary_key=True)
    resolution: Mapped[str] = mapped_column(Text, primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bucket_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_seconds: Mapped[float] = mapped_column(Double)
    bikes_seconds: Mapped[float] = mapped_column(Double)
    docks_seconds: Mapped[float] = mapped_column(Double)
    docks_observed_seconds: Mapped[float] = mapped_column(Double)
    empty_seconds: Mapped[float] = mapped_column(Double)
    full_seconds: Mapped[float] = mapped_column(Double)
    bikes_min: Mapped[int | None] = mapped_column(Integer)
    bikes_max: Mapped[int | None] = mapped_column(Integer)
    docks_min: Mapped[int | None] = mapped_column(Integer)
    docks_max: Mapped[int | None] = mapped_column(Integer)
    samples: Mapped[int] = mapped_column(Integer)
    bikes_added: Mapped[int] = mapped_column(Integer)
    bikes_removed: Mapped[int] = mapped_column(Integer)


class RollupCursor(Base):
    __tablename__ = "rollup_cursor"
    __table_args__ = {"schema": "mart"}

    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    processed_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
