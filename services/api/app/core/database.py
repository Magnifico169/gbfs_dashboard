"""Подключение к PostgreSQL: один движок и одна фабрика сессий на процесс."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config.settings import Settings, get_settings


class Database:
    def __init__(self, settings: Settings) -> None:
        connect_args = {"options": f"-c role={settings.db_role}"} if settings.db_role else {}
        self.engine: Engine = create_engine(
            settings.database_url,
            pool_pre_ping=settings.db_pool_pre_ping,
            connect_args=connect_args,
        )
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Сессия в одной транзакции: коммит при выходе, откат при исключении."""
        with self._sessions.begin() as session:
            yield session

    def session(self) -> Session:
        return self._sessions()

    def dispose(self) -> None:
        self.engine.dispose()


@lru_cache
def get_database() -> Database:
    """Единственный экземпляр на процесс (singleton)."""
    return Database(get_settings())
