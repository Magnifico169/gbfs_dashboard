"""Транзакционный сигнал: PostgreSQL доставляет NOTIFY только после commit."""

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def snapshot_ready(session: Session, system_id: str) -> None:
    session.execute(select(func.pg_notify("snapshot_ready", json.dumps({"system_id": system_id}))))
