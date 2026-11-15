"""Контекст доступа берётся только из проверенного JWT, а не из query-параметров."""

import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Access:
    role: Literal["operator", "manager", "admin"]
    system_id: str | None
    regions: tuple[str, ...] | None

    def apply(self, session: Session) -> None:
        role = {"operator": "app_operator", "manager": "app_manager", "admin": "app_admin"}[
            self.role
        ]
        session.execute(text(f"set local role {role}"))
        for key, value in (
            ("app.system_id", self.system_id or ""),
            ("app.regions", json.dumps(self.regions)),
        ):
            session.execute(
                text("select set_config(:key, :value, true)"), {"key": key, "value": value}
            )
