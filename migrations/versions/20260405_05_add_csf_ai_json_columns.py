"""add crm_autofill, ai_field_corrections, corrected_json, field_validation JSONB columns to csf

Revision ID: 20260405_05
Revises: 20260405_04
Create Date: 2026-04-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "20260405_05"
down_revision: Union[str, None] = "20260405_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    cols = _existing_columns("csf")
    if "crm_autofill" not in cols:
        op.add_column("csf", sa.Column("crm_autofill", JSONB, nullable=True))
    if "ai_field_corrections" not in cols:
        op.add_column("csf", sa.Column("ai_field_corrections", JSONB, nullable=True))
    if "corrected_json" not in cols:
        op.add_column("csf", sa.Column("corrected_json", JSONB, nullable=True))
    if "field_validation" not in cols:
        op.add_column("csf", sa.Column("field_validation", JSONB, nullable=True))


def downgrade() -> None:
    cols = _existing_columns("csf")
    if "field_validation" in cols:
        op.drop_column("csf", "field_validation")
    if "corrected_json" in cols:
        op.drop_column("csf", "corrected_json")
    if "ai_field_corrections" in cols:
        op.drop_column("csf", "ai_field_corrections")
    if "crm_autofill" in cols:
        op.drop_column("csf", "crm_autofill")
