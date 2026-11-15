from typing import Any

from pydantic import BaseModel, Field

from app.schemas.gbfs import VehicleTypeRow


class FetchResult(BaseModel):
    status: int | None = None  # HTTP-код; None при сетевой ошибке
    body: dict[str, Any] | None = None  # разобранный JSON; None для 304 и ошибок
    etag: str | None = None
    error: str | None = None


class StatusCounts(BaseModel):
    rows_in: int
    rows_written: int
    rows_quarantined: int


class ReferenceResult(BaseModel):
    stations: int
    vehicle_types: list[VehicleTypeRow] | None = Field(default=None)
