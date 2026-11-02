"""Схема M1: core, mart, app, партиции, роли и права.

Таблицы сгенерированы по моделям gbfs_db.models; здесь же — то, чего в моделях нет:
схемы, функция суточных партиций, роли БД и гранты (ADR-0002, п. 3).

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for schema in ("core", "mart", "app"):
        op.execute(sa.schema.CreateSchema(schema))
    op.execute(
        """
        do $$
        begin
          if not exists (select from pg_roles where rolname = 'app_ingest') then
            create role app_ingest nologin;
          end if;
          if not exists (select from pg_roles where rolname = 'app_api') then
            create role app_api nologin;
          end if;
        end
        $$
        """
    )

    op.create_table(
        "ingest_run",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("feed", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("rows_in", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_written", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_quarantined", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("status in ('ok', 'not_modified', 'error')", name="ingest_run_status"),
        sa.PrimaryKeyConstraint("run_id"),
        schema="app",
    )
    op.create_index(
        "ix_ingest_run_system_feed_started",
        "ingest_run",
        ["system_id", "feed", sa.text("started_at desc")],
        unique=False,
        schema="app",
    )
    op.create_table(
        "system_setting",
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("discovery_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("poll_interval_s", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("poll_interval_s > 0", name="system_setting_poll_interval_s_check"),
        sa.PrimaryKeyConstraint("system_id"),
        schema="app",
    )
    op.create_table(
        "region",
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("region_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("system_id", "region_id"),
        schema="core",
    )
    op.create_table(
        "station",
        sa.Column("station_pk", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("short_name", sa.Text(), nullable=True),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("region_id", sa.Text(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("station_pk"),
        schema="core",
    )
    op.create_index(
        "station_current_version_uq",
        "station",
        ["system_id", "station_id"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("valid_to is null"),
    )
    op.create_table(
        "station_current",
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bikes_available", sa.Integer(), nullable=False),
        sa.Column("ebikes_available", sa.Integer(), nullable=True),
        sa.Column("bikes_disabled", sa.Integer(), nullable=True),
        sa.Column("docks_available", sa.Integer(), nullable=True),
        sa.Column("docks_disabled", sa.Integer(), nullable=True),
        sa.Column("is_installed", sa.Boolean(), nullable=False),
        sa.Column("is_renting", sa.Boolean(), nullable=False),
        sa.Column("is_returning", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("system_id", "station_id"),
        schema="core",
    )
    op.create_table(
        "station_event",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("event_id"),
        schema="core",
    )
    op.create_index(
        "ix_station_event_station_time",
        "station_event",
        ["system_id", "station_id", sa.text("occurred_at desc")],
        unique=False,
        schema="core",
    )
    op.create_table(
        "station_quarantine",
        sa.Column("quarantine_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("quarantine_id"),
        schema="core",
    )
    op.create_index(
        "ix_station_quarantine_system_time",
        "station_quarantine",
        ["system_id", sa.text("fetched_at desc")],
        unique=False,
        schema="core",
    )
    op.create_table(
        "station_status",
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bikes_available", sa.Integer(), nullable=False),
        sa.Column("ebikes_available", sa.Integer(), nullable=True),
        sa.Column("bikes_disabled", sa.Integer(), nullable=True),
        sa.Column("docks_available", sa.Integer(), nullable=True),
        sa.Column("docks_disabled", sa.Integer(), nullable=True),
        sa.Column("is_installed", sa.Boolean(), nullable=False),
        sa.Column("is_renting", sa.Boolean(), nullable=False),
        sa.Column("is_returning", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("system_id", "station_id", "fetched_at"),
        schema="core",
        postgresql_partition_by="RANGE (fetched_at)",
    )
    op.create_table(
        "system",
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("operator", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("gbfs_version", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("system_id"),
        schema="core",
    )
    op.create_table(
        "vehicle_type",
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("vehicle_type_id", sa.Text(), nullable=False),
        sa.Column("form_factor", sa.Text(), nullable=False),
        sa.Column("propulsion_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("max_range_meters", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("system_id", "vehicle_type_id"),
        schema="core",
    )

    op.execute("create table core.station_status_default partition of core.station_status default")
    # Суточные партиции создаёт приём перед записью; старые удаляются целиком (ADR-0002, п. 2).
    op.execute(
        """
        create function core.ensure_station_status_partitions(days_ahead integer default 2)
        returns void
        language plpgsql
        security definer
        set search_path = core, pg_temp
        as $$
        declare
          d date;
        begin
          for d in
            select generate_series(
              current_date - 1, current_date + days_ahead, interval '1 day'
            )::date
          loop
            execute format(
              'create table if not exists core.%I partition of core.station_status
                 for values from (%L) to (%L)',
              'station_status_' || to_char(d, 'YYYYMMDD'), d, d + 1
            );
          end loop;
        end
        $$
        """
    )

    op.execute(
        """
        insert into app.system_setting (system_id, discovery_url)
        values ('lyft_nyc', 'https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json')
        """
    )

    op.execute("revoke all on schema core, mart, app from public")
    op.execute("grant usage on schema core, mart, app to app_ingest, app_api")
    # Приём пишет в core/mart и журнал запусков; настройки систем только читает
    op.execute("grant select, insert, update, delete on all tables in schema core to app_ingest")
    op.execute("grant select, insert, update, delete on all tables in schema mart to app_ingest")
    op.execute("grant usage on all sequences in schema core to app_ingest")
    op.execute(
        "grant execute on function core.ensure_station_status_partitions(integer) to app_ingest"
    )
    op.execute("grant select on app.system_setting to app_ingest")
    op.execute("grant select, insert on app.ingest_run to app_ingest")
    op.execute("grant usage on all sequences in schema app to app_ingest")
    # API читает core/mart, пишет только в app
    op.execute("grant select on all tables in schema core, mart to app_api")
    op.execute("grant select, insert, update, delete on all tables in schema app to app_api")
    op.execute("grant usage on all sequences in schema app to app_api")


def downgrade() -> None:
    op.execute("drop function core.ensure_station_status_partitions(integer)")
    op.drop_table("vehicle_type", schema="core")
    op.drop_table("system", schema="core")
    op.drop_table("station_status", schema="core")
    op.drop_index(
        "ix_station_quarantine_system_time", table_name="station_quarantine", schema="core"
    )
    op.drop_table("station_quarantine", schema="core")
    op.drop_index("ix_station_event_station_time", table_name="station_event", schema="core")
    op.drop_table("station_event", schema="core")
    op.drop_table("station_current", schema="core")
    op.drop_index(
        "station_current_version_uq",
        table_name="station",
        schema="core",
        postgresql_where=sa.text("valid_to is null"),
    )
    op.drop_table("station", schema="core")
    op.drop_table("region", schema="core")
    op.drop_table("system_setting", schema="app")
    op.drop_index("ix_ingest_run_system_feed_started", table_name="ingest_run", schema="app")
    op.drop_table("ingest_run", schema="app")
    for schema in ("mart", "core", "app"):
        op.execute(sa.schema.DropSchema(schema))
