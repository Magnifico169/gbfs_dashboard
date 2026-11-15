from pydantic import BaseModel

from app.enums import HealthState


class HealthResponse(BaseModel):
    status: HealthState
