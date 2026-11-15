"""M2: подтверждение фактов, ролл-апы и отдельная роль аналитики.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.create_index(
        "ix_station_quarantine_station_time",
        "station_quarantine",
        ["system_id", "station_id", sa.text("fetched_at desc")],
        schema="core",
    )
    op.add_column(
        "station_status", sa.Column("observed_until", sa.DateTime(timezone=True)), schema="core"
    )
    # Старые факты подтверждены только на момент их записи; пробелы не выдумываем.
    op.execute(
        "update core.station_status set observed_until = least(fetched_at, reported_at) "
        "where reported_at is not null"
    )
    op.create_table(
        "station_rollup",
        *timestamps(),
        sa.Column("system_id", sa.Text(), primary_key=True),
        sa.Column("station_id", sa.Text(), primary_key=True),
        sa.Column("resolution", sa.Text(), primary_key=True),
        sa.Column("bucket_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("bucket_end", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.Column(name, sa.Double(), nullable=False)
            for name in (
                "observed_seconds",
                "bikes_seconds",
                "docks_seconds",
                "docks_observed_seconds",
                "empty_seconds",
                "full_seconds",
            )
        ),
        *(
            sa.Column(name, sa.Integer())
            for name in ("bikes_min", "bikes_max", "docks_min", "docks_max")
        ),
        *(
            sa.Column(name, sa.Integer(), nullable=False)
            for name in ("samples", "bikes_added", "bikes_removed")
        ),
        sa.CheckConstraint("resolution in ('10m', 'hour', 'day')", name="rollup_resolution"),
        schema="mart",
    )
    op.create_table(
        "rollup_cursor",
        *timestamps(),
        sa.Column("system_id", sa.Text(), primary_key=True),
        sa.Column("processed_until", sa.DateTime(timezone=True), nullable=False),
        schema="mart",
    )
    op.execute(
        "do $$ begin if not exists (select from pg_roles where rolname='app_analytics') "
        "then create role app_analytics nologin; end if; end $$"
    )
    op.execute("grant usage on schema core, mart to app_analytics")
    op.execute("grant select on all tables in schema core to app_analytics")
    op.execute("revoke all on all tables in schema mart from app_ingest")
    op.execute("grant select, insert, update, delete on all tables in schema mart to app_analytics")
    op.execute("grant select on all tables in schema mart to app_api")


def downgrade() -> None:
    op.drop_index(
        "ix_station_quarantine_station_time", table_name="station_quarantine", schema="core"
    )
    op.drop_table("rollup_cursor", schema="mart")
    op.drop_table("station_rollup", schema="mart")
    op.drop_column("station_status", "observed_until", schema="core")
    op.execute("revoke select on all tables in schema core from app_analytics")
    op.execute("revoke usage on schema core, mart from app_analytics")
