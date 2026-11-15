"""Подключение к PostgreSQL: один движок и одна фабрика сессий на процесс."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import ClassVar

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config.settings import Settings, get_settings


class Database:
    _instance: ClassVar["Database | None"] = None
    _instance_lock: ClassVar[Lock] = Lock()

    @classmethod
    def instance(cls) -> "Database":
        """Один менеджер соединений, в том числе при параллельных первых запросах."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(get_settings())
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Освобождение пула и экземпляра при смене окружения в тестах."""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.dispose()
                cls._instance = None

    def __init__(self, settings: Settings, *, pool_size: int = 5, max_overflow: int = 10) -> None:
        options = f"-c statement_timeout={settings.db_statement_timeout_ms}"
        if settings.db_role:
            options += f" -c role={settings.db_role}"
        connect_args = {"options": options, "connect_timeout": settings.db_connect_timeout_s}
        self.engine: Engine = create_engine(
            settings.database_url,
            pool_pre_ping=settings.db_pool_pre_ping,
            connect_args=connect_args,
            pool_timeout=settings.db_connect_timeout_s,
            pool_size=pool_size,
            max_overflow=max_overflow,
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


def get_database() -> Database:
    """Единственный экземпляр на процесс (singleton)."""
    return Database.instance()
