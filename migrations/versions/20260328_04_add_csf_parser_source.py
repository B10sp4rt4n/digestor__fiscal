"""add csf parser source column

Revision ID: 20260328_04
Revises: 20260328_03
Create Date: 2026-03-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260328_04"
down_revision: Union[str, None] = "20260328_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing = _existing_columns("csf")

    if "parser_source" not in existing:
        op.add_column(
            "csf",
            sa.Column("parser_source", sa.String(), nullable=False, server_default="regex"),
        )

    op.execute("UPDATE csf SET parser_source = COALESCE(parser_source, 'regex')")


def downgrade() -> None:
    existing = _existing_columns("csf")

    if "parser_source" in existing:
        op.drop_column("csf", "parser_source")