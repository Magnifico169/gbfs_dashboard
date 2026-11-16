"""Материализованная аналитика M5; единственный писатель — R."""

from datetime import datetime

from sqlalchemy import DateTime, Double, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StationInsight(Base):
    __tablename__ = "station_insight"
    __table_args__ = {"schema": "mart"}
    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    station_id: Mapped[str] = mapped_column(Text, primary_key=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(Text)
    age_seconds: Mapped[float | None] = mapped_column(Double)
    occupancy: Mapped[float | None] = mapped_column(Double)
    empty_seconds: Mapped[float] = mapped_column(Double)
    full_seconds: Mapped[float] = mapped_column(Double)
    disabled: Mapped[int | None] = mapped_column(Integer)
    renting: Mapped[bool | None]
    electric_share: Mapped[float | None] = mapped_column(Double)
    profile: Mapped[list] = mapped_column(JSONB)


class LoadForecast(Base):
    __tablename__ = "load_forecast"
    __table_args__ = {"schema": "mart"}
    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    station_id: Mapped[str] = mapped_column(Text, primary_key=True)
    anchor: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    history_days: Mapped[int] = mapped_column(Integer)
    points: Mapped[list] = mapped_column(JSONB)
