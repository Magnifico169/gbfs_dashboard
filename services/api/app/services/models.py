"""Модели состояния сервисов, не являющиеся таблицами БД."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.gbfs import VehicleTypeRow

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class SystemState(BaseModel):
    """Состояние опроса одной системы между циклами."""

    system_id: str
    discovery_url: str
    poll_interval_s: int | None = None
    version: str | None = None
    feed_urls: dict[str, str] = Field(default_factory=dict)
    vehicle_types: list[VehicleTypeRow] | None = None
    etags: dict[str, str] = Field(default_factory=dict)
    bodies: dict[str, dict[str, Any]] = Field(default_factory=dict)
    reference_at: datetime = EPOCH
    next_due: datetime = EPOCH
    status_checked_at: datetime | None = None
    ttl: int = 0
