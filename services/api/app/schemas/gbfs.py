"""Нормализованная модель данных GBFS: одна и та же для v1.1, v2.x и v3.0.

Различия версий закрыты алиасами: в v3 num_bikes_available / num_bikes_disabled
переименованы в num_vehicles_available / num_vehicles_disabled.
"""

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.schemas.types import (
    LenientDatetime,
    LenientFlag,
    LenientFloat,
    LenientInt,
    LenientStr,
    LocalizedText,
    RequiredFloat,
    RequiredLocalizedText,
    RequiredStr,
)


class Row(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Discovery(BaseModel):
    version: str | None
    feeds: dict[str, str]


class SystemInfo(Row):
    system_id: str
    name: LocalizedText = None
    operator: LocalizedText = None
    timezone: LenientStr = None
    language: LenientStr = None


class RegionRow(Row):
    system_id: str
    region_id: RequiredStr
    name: RequiredLocalizedText


class VehicleTypeRow(Row):
    system_id: str
    vehicle_type_id: RequiredStr
    form_factor: RequiredStr
    propulsion_type: RequiredStr
    name: LocalizedText = None
    max_range_meters: LenientFloat = None


class StationInfoRow(Row):
    system_id: str
    station_id: RequiredStr
    name: RequiredLocalizedText
    short_name: LocalizedText = None
    lat: RequiredFloat
    lon: RequiredFloat
    address: LenientStr = None
    region_id: LenientStr = None
    capacity: LenientInt = None


class StationStatusRow(Row):
    """Снимок станции. Обязательность полей проверяет валидатор (services.validation):
    строка с пропусками должна дойти до карантина с причиной, а не пропасть при разборе.
    """

    system_id: str
    station_id: LenientStr = None
    reported_at: LenientDatetime = Field(
        None, validation_alias=AliasChoices("reported_at", "last_reported")
    )
    bikes_available: LenientInt = Field(
        None,
        validation_alias=AliasChoices(
            "bikes_available", "num_bikes_available", "num_vehicles_available"
        ),
    )
    ebikes_available: LenientInt = Field(
        None, validation_alias=AliasChoices("ebikes_available", "num_ebikes_available")
    )
    bikes_disabled: LenientInt = Field(
        None,
        validation_alias=AliasChoices(
            "bikes_disabled", "num_bikes_disabled", "num_vehicles_disabled"
        ),
    )
    docks_available: LenientInt = Field(
        None, validation_alias=AliasChoices("docks_available", "num_docks_available")
    )
    docks_disabled: LenientInt = Field(
        None, validation_alias=AliasChoices("docks_disabled", "num_docks_disabled")
    )
    is_installed: LenientFlag = None
    is_renting: LenientFlag = None
    is_returning: LenientFlag = None
    # Нужны только для подсчёта электрических по типам транспорта; в БД не пишутся
    vehicle_types_available: list[dict[str, Any]] | None = Field(None, exclude=True)
