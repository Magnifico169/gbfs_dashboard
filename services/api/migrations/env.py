from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config.settings import get_settings
from app.models import Base

MANAGED_SCHEMAS = {"core", "app", "mart"}


def include_object(obj, name, type_, reflected, compare_to):
    # Партиции создаются функцией ensure_station_status_partitions и в моделях не описаны
    if type_ == "table" and reflected and name.startswith("station_status_"):
        return False
    schema = getattr(obj, "schema", None)
    return type_ != "table" or schema in MANAGED_SCHEMAS


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            include_schemas=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
