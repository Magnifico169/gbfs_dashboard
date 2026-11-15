from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.core.database import get_database


def get_session() -> Iterator[Session]:
    with get_database().transaction() as session:
        yield session
