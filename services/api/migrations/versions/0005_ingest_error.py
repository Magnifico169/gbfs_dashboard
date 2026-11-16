"""Сохранить сообщения ошибок ранних установок и выровнять новые базы.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    columns = sa.inspect(op.get_bind()).get_columns("ingest_run", schema="app")
    if not any(column["name"] == "error" for column in columns):
        op.add_column("ingest_run", sa.Column("error", sa.Text()), schema="app")


def downgrade():
    op.drop_column("ingest_run", "error", schema="app")
