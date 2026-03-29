"""add csf processing status columns

Revision ID: 20260328_03
Revises: 20260327_02
Create Date: 2026-03-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260328_03"
down_revision: Union[str, None] = "20260327_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing = _existing_columns("csf")

    if "processing_status" not in existing:
        op.add_column(
            "csf",
            sa.Column("processing_status", sa.String(), nullable=False, server_default="processed"),
        )
    if "status_reason" not in existing:
        op.add_column("csf", sa.Column("status_reason", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE csf
        SET processing_status = CASE
            WHEN qr_valid = TRUE THEN 'processed'
            WHEN qr_valid = FALSE THEN 'needs_review'
            WHEN source_filename IS NOT NULL THEN 'pending_qr'
            ELSE 'incomplete'
        END,
        status_reason = CASE
            WHEN qr_valid = TRUE THEN NULL
            WHEN qr_valid = FALSE THEN 'qr_invalid'
            WHEN source_filename IS NOT NULL THEN 'qr_not_detected'
            ELSE 'missing_source_file'
        END
        """
    )


def downgrade() -> None:
    existing = _existing_columns("csf")

    if "status_reason" in existing:
        op.drop_column("csf", "status_reason")
    if "processing_status" in existing:
        op.drop_column("csf", "processing_status")
