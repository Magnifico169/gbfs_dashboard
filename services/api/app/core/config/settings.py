"""Все настраиваемые параметры сервиса. Значения берутся из переменных окружения (и `.env`
при локальном запуске); имена PG* и POSTGRES_* равноправны, чтобы один `.env` служил и
compose, и локальной разработке.
"""

from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # PostgreSQL
    pg_host: str = Field("localhost", validation_alias=AliasChoices("PGHOST"))
    pg_port: int = Field(5432, validation_alias=AliasChoices("PGPORT", "POSTGRES_PORT"))
    pg_user: str = Field("gbfs", validation_alias=AliasChoices("PGUSER", "POSTGRES_USER"))
    pg_password: SecretStr = Field(
        SecretStr(""), validation_alias=AliasChoices("PGPASSWORD", "POSTGRES_PASSWORD")
    )
    pg_database: str = Field("gbfs", validation_alias=AliasChoices("PGDATABASE", "POSTGRES_DB"))
    # Роль БД, под которой работает сервис (app_ingest / app_api). None — роль входа как есть
    # (так работают миграции).
    db_role: str | None = None
    db_pool_pre_ping: bool = True

    # HTTP-клиент к фидам GBFS
    http_timeout_s: float = Field(15.0, gt=0)
    http_max_tries: int = Field(3, ge=1)
    http_user_agent: str = "gbfs-dashboard-ingest/0.1"

    # Приём данных
    poll_timeout_s: int = Field(30, gt=0)
    reference_refresh_s: int = 3600
    worker_tick_s: float = 1.0
    partitions_days_ahead: int = 2
    electric_propulsion_types: frozenset[str] = frozenset({"electric_assist", "electric"})

    # Валидация снимков
    future_tolerance_s: int = 300

    # Чтение и доставка обновлений
    stale_after_s: int = Field(300, gt=0)
    read_cache_ttl_s: float = Field(3.0, gt=0)
    read_cache_size: int = Field(256, ge=1)
    read_concurrency: int = Field(16, ge=1)
    sse_max_clients: int = Field(2000, ge=1)
    sse_queue_size: int = Field(16, ge=1)
    sse_heartbeat_s: float = Field(15.0, gt=0)
    db_connect_timeout_s: int = Field(3, ge=1)
    db_statement_timeout_ms: int = Field(5000, ge=1)

    # Приложение
    log_level: str = "INFO"
    app_title: str = "GBFS Dashboard"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.pg_user,
            password=self.pg_password.get_secret_value(),
            host=self.pg_host,
            port=self.pg_port,
            database=self.pg_database,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
