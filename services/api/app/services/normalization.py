"""Нормализация фидов GBFS в единую модель (app.schemas.gbfs). Разбор значений и различия
версий описаны в самих схемах; здесь — обход фидов и пропуск непригодных записей.
"""

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.schemas.gbfs import (
    Discovery,
    RegionRow,
    StationInfoRow,
    StationStatusRow,
    SystemInfo,
    VehicleTypeRow,
)
from app.schemas.types import to_int, to_str


def _items(feed: dict[str, Any], key: str) -> list[Any]:
    return (feed.get("data") or {}).get(key) or []


def _parse_all[T: BaseModel](model: type[T], items: Iterable[Any], system_id: str) -> list[T]:
    """Записи, не прошедшие разбор (нет обязательных атрибутов), пропускаются."""
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            rows.append(model.model_validate({**item, "system_id": system_id}))
        except ValidationError:
            continue
    return rows


def parse_discovery(feed: dict[str, Any]) -> Discovery:
    data = feed.get("data") or {}
    feeds = data.get("feeds")
    if feeds is None:  # v1/v2: фиды сгруппированы по языкам
        language = "en" if "en" in data else next(iter(data), None)
        feeds = (data.get(language) or {}).get("feeds", [])
    urls = {item["name"]: item["url"] for item in feeds if "name" in item and "url" in item}
    return Discovery(version=to_str(feed.get("version")), feeds=urls)


def normalize_system_information(feed: dict[str, Any], system_id: str) -> SystemInfo:
    return SystemInfo.model_validate({**(feed.get("data") or {}), "system_id": system_id})


def normalize_regions(feed: dict[str, Any], system_id: str) -> list[RegionRow]:
    return _parse_all(RegionRow, _items(feed, "regions"), system_id)


def normalize_vehicle_types(feed: dict[str, Any], system_id: str) -> list[VehicleTypeRow]:
    return _parse_all(VehicleTypeRow, _items(feed, "vehicle_types"), system_id)


def normalize_station_information(feed: dict[str, Any], system_id: str) -> list[StationInfoRow]:
    return _parse_all(StationInfoRow, _items(feed, "stations"), system_id)


def electric_vehicle_type_ids(
    vehicle_types: list[VehicleTypeRow], electric_propulsion: frozenset[str]
) -> set[str]:
    return {v.vehicle_type_id for v in vehicle_types if v.propulsion_type in electric_propulsion}


def _electric_by_type(row: StationStatusRow, electric_ids: set[str]) -> int | None:
    counts = [
        to_int(item.get("count"))
        for item in row.vehicle_types_available or []
        if to_str(item.get("vehicle_type_id")) in electric_ids
    ]
    return None if None in counts else sum(counts)  # type: ignore[arg-type]


def normalize_station_status(
    feed: dict[str, Any],
    system_id: str,
    vehicle_types: list[VehicleTypeRow] | None,
    electric_propulsion: frozenset[str],
) -> list[StationStatusRow]:
    """Электрические считаются по vehicle_types_available, если известны типы транспорта,
    иначе берётся устаревшее num_ebikes_available (v1.1, Citi Bike).
    """
    rows = _parse_all(StationStatusRow, _items(feed, "stations"), system_id)
    if vehicle_types is not None:
        electric_ids = electric_vehicle_type_ids(vehicle_types, electric_propulsion)
        for row in rows:
            if row.vehicle_types_available is not None:
                row.ebikes_available = _electric_by_type(row, electric_ids)
    return rows
