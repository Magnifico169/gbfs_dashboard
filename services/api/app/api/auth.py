from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config.settings import get_settings
from app.services.auth import Principal, decode_token, invalid_credentials

bearer = HTTPBearer(auto_error=False)


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None:
        raise invalid_credentials()
    return decode_token(credentials.credentials, get_settings())


CurrentUser = Annotated[Principal, Depends(current_user)]


def require_admin(user: CurrentUser) -> Principal:
    if user.role != "admin":
        raise HTTPException(403, "role_forbidden")
    return user


Admin = Annotated[Principal, Depends(require_admin)]


def data_access(user: CurrentUser, system_id: str | None = None):
    if user.role != "admin" and system_id is not None and user.system_id != system_id:
        raise HTTPException(403, "system_forbidden")
    if user.role != "admin" and user.regions == []:
        raise HTTPException(403, "scope_empty")
    return user.access


def live_access(user: CurrentUser, system_id: str):
    access = data_access(user, system_id)
    if user.role == "manager":
        raise HTTPException(403, "role_forbidden")
    return access
