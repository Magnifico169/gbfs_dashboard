import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core.config.settings import Settings, get_settings
from app.core.database import Database, get_database

API_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(os.environ.get("FIXTURES_DIR", API_DIR.parents[1] / "fixtures"))
INGEST_ROLE = "app_ingest"


def read_fixture(feed_set: str, name: str) -> dict:
    return json.loads((FIXTURES / feed_set / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def pg_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Отдельная база на тест: создаётся, мигрируется Alembic и удаляется."""
    name = f"gbfs_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(get_settings().database_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"create database {name}"))
    except OperationalError:
        admin.dispose()
        pytest.skip("PostgreSQL недоступна (PG*)")
    monkeypatch.setenv("PGDATABASE", name)
    get_settings.cache_clear()
    config = Config(str(API_DIR / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        yield name
    finally:
        get_database.cache_clear()
        get_settings.cache_clear()
        with admin.connect() as conn:
            conn.execute(text(f"drop database if exists {name} with (force)"))
        admin.dispose()


@pytest.fixture
def ingest_db(pg_database: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Database]:
    """Соединение под ролью приёма: границу прав проверяет сама БД."""
    monkeypatch.setenv("DB_ROLE", INGEST_ROLE)
    get_settings.cache_clear()
    get_database.cache_clear()
    database = get_database()
    yield database
    database.dispose()
