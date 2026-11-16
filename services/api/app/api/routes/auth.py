from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.auth import CurrentUser
from app.api.deps import get_session
from app.core.config.settings import get_settings
from app.schemas.auth import Login, Refresh, Register, TokenPair
from app.services import auth, registration

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
SessionDep = Annotated[Session, Depends(get_session, scope="function")]


def private(response: Response):
    response.headers["Cache-Control"] = "no-store"


@router.post("/login", response_model=TokenPair)
def login(body: Login, session: SessionDep, response: Response):
    private(response)
    return auth.login(
        session, body.username, body.password.get_secret_value(), body.system_id, get_settings()
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(body: Refresh, session: SessionDep, response: Response):
    private(response)
    return auth.refresh(session, body.refresh_token.get_secret_value(), get_settings())


@router.post("/logout", status_code=204)
def logout(body: Refresh, session: SessionDep):
    auth.logout(session, body.refresh_token.get_secret_value())


@router.get("/me")
def me(user: CurrentUser, response: Response):
    private(response)
    return {
        "id": int(user.sub),
        "role": user.role,
        "system_id": user.system_id,
        "region_ids": user.regions,
        "expires_at": user.exp,
    }


@router.get("/systems")
def registration_systems(session: SessionDep, response: Response):
    private(response)
    return registration.systems(session)


@router.post("/register", response_model=TokenPair, status_code=201)
def register(body: Register, session: SessionDep, response: Response):
    private(response)
    return registration.register(session, body, get_settings())
