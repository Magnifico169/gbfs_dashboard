from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

RoleName = Literal["operator", "manager", "admin"]
Username = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.@-]+$"
    ),
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: Username
    password: SecretStr = Field(min_length=1, max_length=1024)
    system_id: str | None = Field(None, min_length=1, max_length=200)


class Register(Input):
    username: Username
    password: SecretStr = Field(min_length=12, max_length=1024)
    system_id: str = Field(min_length=1, max_length=200)


class Refresh(Input):
    refresh_token: SecretStr = Field(min_length=1, max_length=200)


class UserCreate(Input):
    username: Username
    password: SecretStr = Field(min_length=12, max_length=1024)
    role: RoleName


class UserUpdate(Input):
    role: RoleName | None = None
    active: bool | None = None
    password: SecretStr | None = Field(None, min_length=12, max_length=1024)


class ZoneCreate(Input):
    system_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    region_ids: list[str] | None = Field(None, max_length=500)


class ScopePut(Input):
    zone_id: int = Field(gt=0)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 — имя схемы авторизации
    expires_in: int
