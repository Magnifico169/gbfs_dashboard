"""Обновление установленной до ORM-рефакторинга базы с номером 0001."""

import pytest
from alembic import command
from alembic.config import Config
from conftest import API_DIR
from sqlalchemy import create_engine, inspect, text

from app.core.config.settings import get_settings


@pytest.mark.parametrize("stop_at_m2", [False, True])
def test_legacy_m1_upgrade_preserves_records_and_matches_models(pg_database, stop_at_m2):
    config = Config(str(API_DIR / "alembic.ini"))
    command.downgrade(config, "0001")
    engine = create_engine(get_settings().database_url)
    tables = (
        ("core", "system", "system_id"),
        ("core", "region", "region_id"),
        ("core", "vehicle_type", "vehicle_type_id"),
        ("core", "station", "station_pk"),
        ("core", "station_event", "event_id"),
        ("core", "station_quarantine", "quarantine_id"),
        ("core", "station_current", None),
        ("core", "station_status", None),
        ("app", "system_setting", None),
        ("app", "ingest_run", "run_id"),
    )
    try:
        with engine.begin() as conn:
            for schema, table, old_name in tables:
                if old_name:
                    conn.execute(
                        text(f"alter table {schema}.{table} rename column id to {old_name}")
                    )
                conn.execute(text(f"alter table {schema}.{table} drop column created_at"))
                if table not in ("system", "system_setting"):
                    conn.execute(text(f"alter table {schema}.{table} drop column updated_at"))
            for table, key in (
                ("core.station", "station_pk"),
                ("core.station_event", "event_id"),
                ("core.station_quarantine", "quarantine_id"),
                ("app.ingest_run", "run_id"),
            ):
                conn.execute(text(f"alter table {table} alter column {key} set generated always"))
            conn.execute(text("alter table app.ingest_run add column error text"))
            conn.execute(
                text("alter table core.station_quarantine alter column payload set not null")
            )
            conn.execute(
                text(
                    "alter index core.ix_station_event_station_time rename to "
                    "station_event_system_id_station_id_occurred_at_idx"
                )
            )
            conn.execute(
                text(
                    "alter index core.ix_station_quarantine_system_time rename to "
                    "station_quarantine_system_id_fetched_at_idx"
                )
            )
            conn.execute(
                text(
                    "alter index app.ix_ingest_run_system_feed_started rename to "
                    "ingest_run_system_id_feed_started_at_idx"
                )
            )
            conn.execute(
                text(
                    "alter table app.ingest_run rename constraint ingest_run_status "
                    "to ingest_run_status_check"
                )
            )
            conn.execute(
                text(
                    "insert into core.system (system_id,name,timezone,gbfs_version) "
                    "values ('legacy','Legacy','UTC','2.3')"
                )
            )
            station_id = conn.execute(
                text(
                    "insert into core.station (system_id,station_id,name,lat,lon,valid_from) "
                    "values ('legacy','station','Station',1,2,'2026-01-01Z') returning station_pk"
                )
            ).scalar_one()
            conn.execute(
                text(
                    "insert into core.station_status "
                    "(system_id,station_id,reported_at,fetched_at,bikes_available,is_installed,"
                    "is_renting,is_returning) values ('legacy','station','2026-01-01Z',"
                    "'2026-01-01Z',7,true,true,true)"
                )
            )
            run_id = conn.execute(
                text(
                    "insert into app.ingest_run "
                    "(system_id,feed,started_at,finished_at,status,error) "
                    "values ('legacy','station_status','2026-01-01Z','2026-01-01Z','error',"
                    "'Original error') returning run_id"
                )
            ).scalar_one()
        if stop_at_m2:
            command.upgrade(config, "0002")
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.execute(text("select id from core.system")).scalar_one() == "legacy"
            assert conn.execute(text("select id from core.station")).scalar_one() == station_id
            assert conn.execute(text("select id,error from app.ingest_run")).one() == (
                run_id,
                "Original error",
            )
            assert (
                conn.execute(text("select bikes_available from core.station_status")).scalar_one()
                == 7
            )
            assert conn.execute(text("select system_id from app.zone")).scalar_one() == "legacy"
            assert (
                not inspect(conn)
                .get_columns("station", schema="core")[0]
                .get("identity", {})
                .get("always", False)
            )
        command.check(config)
    finally:
        engine.dispose()
