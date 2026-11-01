"""Схема M1: core, mart, app, роли и права.

Revision ID: 0001
Revises:
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (Path(__file__).with_suffix(".sql")).read_text(encoding="utf-8")
    op.execute(sa.text(sql))


def downgrade() -> None:
    op.execute("drop schema core, mart, app cascade")
