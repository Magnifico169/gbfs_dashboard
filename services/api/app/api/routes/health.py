from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.enums import HealthState
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/livez", response_model=HealthResponse)
def livez() -> HealthResponse:
    """Процесс жив."""
    return HealthResponse(status=HealthState.OK)


@router.get("/readyz", response_model=HealthResponse)
def readyz(session: Annotated[Session, Depends(get_session)]) -> HealthResponse:
    """Готов принимать трафик: база отвечает."""
    try:
        session.execute(select(1))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=HealthState.UNAVAILABLE.value
        ) from exc
    return HealthResponse(status=HealthState.OK)
