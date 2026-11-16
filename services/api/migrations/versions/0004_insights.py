"""M4/M5: материализованные профили, внимание и прогнозы с RLS.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def common():
    return [
        sa.Column("system_id", sa.Text(), primary_key=True),
        sa.Column("station_id", sa.Text(), primary_key=True),
        *[
            sa.Column(
                n, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
            )
            for n in ("created_at", "updated_at")
        ],
    ]


def upgrade():
    for name in ("ebikes_seconds", "ebikes_observed_seconds", "bikes_with_ebikes_seconds"):
        op.add_column(
            "station_rollup",
            sa.Column(name, sa.Double(), nullable=False, server_default="0"),
            schema="mart",
        )
    op.create_table(
        "station_insight",
        *common(),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.Text(), nullable=False),
        sa.Column("age_seconds", sa.Double()),
        sa.Column("occupancy", sa.Double()),
        sa.Column("empty_seconds", sa.Double(), nullable=False),
        sa.Column("full_seconds", sa.Double(), nullable=False),
        sa.Column("disabled", sa.Integer()),
        sa.Column("renting", sa.Boolean()),
        sa.Column("electric_share", sa.Double()),
        sa.Column("profile", JSONB(), nullable=False),
        schema="mart",
    )
    op.create_table(
        "load_forecast",
        *common(),
        sa.Column("anchor", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("history_days", sa.Integer(), nullable=False),
        sa.Column("points", JSONB(), nullable=False),
        schema="mart",
    )
    for name in ("station_insight", "load_forecast"):
        table = f"mart.{name}"
        op.execute(f"grant select, insert, update, delete on {table} to app_analytics")
        op.execute(f"grant select on {table} to app_operator, app_manager, app_admin")
        op.execute(f"alter table {table} enable row level security")
        op.execute(
            f"create policy service_access on {table} to app_analytics, app_admin "
            "using (true) with check (true)"
        )
        op.execute(
            f"create policy scoped_read on {table} for select to app_operator, app_manager "  # noqa: S608 — фиксированные имена
            "using (system_id = nullif(current_setting('app.system_id', true), '') "
            "and exists (select 1 from core.station s where "
            f"s.system_id = {table}.system_id and s.station_id = {table}.station_id "
            "and s.valid_to is null))"
        )


def downgrade():
    for name in ("ebikes_seconds", "ebikes_observed_seconds", "bikes_with_ebikes_seconds"):
        op.drop_column("station_rollup", name, schema="mart")
    op.drop_table("load_forecast", schema="mart")
    op.drop_table("station_insight", schema="mart")
