"""Справочники (схема core): системы, регионы, типы транспорта, станции (SCD2)."""

from datetime import datetime

from sqlalchemy import BigInteger, Double, Identity, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TIMESTAMPTZ, Base


class System(Base):
    __tablename__ = "system"
    __table_args__ = {"schema": "core"}

    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    gbfs_version: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())


class Region(Base):
    __tablename__ = "region"
    __table_args__ = {"schema": "core"}

    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    region_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class VehicleType(Base):
    __tablename__ = "vehicle_type"
    __table_args__ = {"schema": "core"}

    system_id: Mapped[str] = mapped_column(Text, primary_key=True)
    vehicle_type_id: Mapped[str] = mapped_column(Text, primary_key=True)
    form_factor: Mapped[str] = mapped_column(Text)
    propulsion_type: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    max_range_meters: Mapped[float | None] = mapped_column(Double)


class Station(Base):
    """SCD2: при изменении атрибутов текущая версия закрывается, открывается новая."""

    __tablename__ = "station"
    __table_args__ = (
        Index(
            "station_current_version_uq",
            "system_id",
            "station_id",
            unique=True,
            postgresql_where=text("valid_to is null"),
        ),
        {"schema": "core"},
    )

    station_pk: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    system_id: Mapped[str] = mapped_column(Text)
    station_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    short_name: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Double)
    lon: Mapped[float] = mapped_column(Double)
    address: Mapped[str | None] = mapped_column(Text)
    region_id: Mapped[str | None] = mapped_column(Text)
    capacity: Mapped[int | None] = mapped_column(Integer)
    valid_from: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    valid_to: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
