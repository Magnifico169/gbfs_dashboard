"""M3: пользователи, зоны, сеансы, RLS и роли чтения.

Revision ID: 0003
Revises: 0002a
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002a"
branch_labels = None
depends_on = None

READ_ROLES = ("app_operator", "app_manager", "app_admin")
TABLES = (
    "core.system",
    "core.region",
    "core.vehicle_type",
    "core.station",
    "core.station_current",
    "core.station_status",
    "core.station_event",
    "core.station_quarantine",
    "mart.station_rollup",
    "mart.rollup_cursor",
)


def timestamps():
    return [
        sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))
        for name in ("created_at", "updated_at")
    ]


def identity():
    return sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True)


def upgrade():
    op.create_table(
        "role", *timestamps(), sa.Column("id", sa.Text(), primary_key=True), schema="app"
    )
    op.execute("insert into app.role (id) values ('operator'), ('manager'), ('admin')")
    op.create_table(
        "user_account",
        *timestamps(),
        identity(),
        sa.Column("username", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role_id", sa.Text(), sa.ForeignKey("app.role.id"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        schema="app",
    )
    op.create_table(
        "zone",
        *timestamps(),
        identity(),
        sa.Column("system_id", sa.Text(), sa.ForeignKey("core.system.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("region_ids", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("id", "system_id"),
        sa.UniqueConstraint("system_id", "name"),
        schema="app",
    )
    op.create_table(
        "user_scope",
        *timestamps(),
        sa.Column(
            "user_id", sa.BigInteger(), sa.ForeignKey("app.user_account.id"), primary_key=True
        ),
        sa.Column("system_id", sa.Text(), sa.ForeignKey("core.system.id"), primary_key=True),
        sa.Column("zone_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["zone_id", "system_id"], ["app.zone.id", "app.zone.system_id"]),
        schema="app",
    )
    op.create_table(
        "auth_session",
        *timestamps(),
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("app.user_account.id"), nullable=False),
        sa.Column("system_id", sa.Text(), sa.ForeignKey("core.system.id")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("ix_app_auth_session_user_id", "auth_session", ["user_id"], schema="app")
    op.create_table(
        "audit_log",
        *timestamps(),
        identity(),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("app.user_account.id")),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        schema="app",
    )
    op.execute(
        "insert into app.zone (system_id, name, region_ids) "
        "select id, 'Вся система', 'null'::jsonb from core.system"
    )
    for role in READ_ROLES:
        op.execute(
            f"do $$ begin if not exists (select from pg_roles where rolname='{role}') "
            f"then create role {role} nologin; end if; end $$"
        )
        op.execute(f"grant usage on schema core, mart to {role}")
        op.execute(f"grant {role} to app_api with inherit false")
    op.execute("grant usage on schema app to app_admin")
    op.execute("revoke select on all tables in schema core, mart from app_api")
    op.execute("grant select on all tables in schema core, mart to app_admin")
    op.execute(
        "grant select on core.system, core.region, core.vehicle_type, core.station, "
        "core.station_current, core.station_status, core.station_event, "
        "core.station_quarantine to app_operator"
    )
    op.execute(
        "grant select on core.system, core.region, core.vehicle_type, core.station to app_manager"
    )
    op.execute("grant select on all tables in schema mart to app_operator, app_manager")
    for table in ("user_account", "zone", "user_scope", "auth_session"):
        op.execute(f"grant select, insert, update, delete on app.{table} to app_api, app_admin")
    op.execute("grant select on app.role to app_api, app_admin")
    op.execute("grant select, insert on app.audit_log to app_api, app_admin")
    op.execute("grant usage on all sequences in schema app to app_api, app_admin")
    for table in TABLES:
        op.execute(f"alter table {table} enable row level security")
        # Сервисы записи сохраняют свой доступ. API без контекста не видит строк.
        op.execute(
            f"create policy service_access on {table} to app_ingest, app_analytics, "
            "app_admin using (true) with check (true)"
        )
        column = "id" if table == "core.system" else "system_id"
        condition = f"{column} = nullif(current_setting('app.system_id', true), '')"
        regions = "nullif(current_setting('app.regions', true), '')::jsonb"
        if table in ("core.station", "core.region"):
            region = "region_id" if table == "core.station" else "id"
            condition += f" and ({regions} = 'null'::jsonb or {regions} ? {region})"
            if table == "core.station":
                condition += " and valid_to is null"
        elif table in (
            "core.station_current",
            "core.station_status",
            "core.station_event",
            "core.station_quarantine",
            "mart.station_rollup",
        ):
            condition += (
                " and exists (select 1 from core.station s where "  # noqa: S608 — статический TABLES
                f"s.system_id = {table}.system_id and s.station_id = {table}.station_id "
                "and s.valid_to is null)"
            )
        op.execute(
            f"create policy scoped_read on {table} for select to app_operator, app_manager "
            f"using ({condition})"
        )
    # Прямые SELECT из партиций не получают грантов ролей пользователей.
    op.execute(
        "revoke execute on function core.ensure_station_status_partitions(integer) from public"
    )


def downgrade():
    for table in reversed(TABLES):
        op.execute(f"drop policy scoped_read on {table}")
        op.execute(f"drop policy service_access on {table}")
        op.execute(f"alter table {table} disable row level security")
    for table in ("audit_log", "auth_session", "user_scope", "zone", "user_account", "role"):
        op.drop_table(table, schema="app")
    for role in READ_ROLES:
        op.execute(f"revoke all on all tables in schema core, mart from {role}")
        op.execute(f"revoke usage on schema core, mart from {role}")
        op.execute(f"revoke {role} from app_api")
    op.execute("revoke usage on all sequences in schema app from app_admin")
    op.execute("revoke usage on schema app from app_admin")
    op.execute("grant select on all tables in schema core, mart to app_api")
    op.execute("grant execute on function core.ensure_station_status_partitions(integer) to public")
