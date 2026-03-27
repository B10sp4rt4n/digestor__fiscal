"""add csf metadata fields

Revision ID: 20260327_01
Revises: 
Create Date: 2026-03-27 05:15:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260327_01"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing = _existing_columns("csf")

    if "id_cif" not in existing:
        op.add_column("csf", sa.Column("id_cif", sa.String(), nullable=True))
    if "source_filename" not in existing:
        op.add_column("csf", sa.Column("source_filename", sa.String(), nullable=True))
    if "extracted_text" not in existing:
        op.add_column("csf", sa.Column("extracted_text", sa.Text(), nullable=True))
    if "qr_text" not in existing:
        op.add_column("csf", sa.Column("qr_text", sa.String(), nullable=True))
    if "qr_valid" not in existing:
        op.add_column("csf", sa.Column("qr_valid", sa.Boolean(), nullable=True))
    if "qr_online" not in existing:
        op.add_column("csf", sa.Column("qr_online", sa.Boolean(), nullable=True))
    if "uploaded_at" not in existing:
        op.add_column("csf", sa.Column("uploaded_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    existing = _existing_columns("csf")

    if "uploaded_at" in existing:
        op.drop_column("csf", "uploaded_at")
    if "qr_online" in existing:
        op.drop_column("csf", "qr_online")
    if "qr_valid" in existing:
        op.drop_column("csf", "qr_valid")
    if "qr_text" in existing:
        op.drop_column("csf", "qr_text")
    if "extracted_text" in existing:
        op.drop_column("csf", "extracted_text")
    if "source_filename" in existing:
        op.drop_column("csf", "source_filename")
    if "id_cif" in existing:
        op.drop_column("csf", "id_cif")
