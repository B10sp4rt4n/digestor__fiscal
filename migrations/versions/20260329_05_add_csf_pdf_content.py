"""add csf pdf_content column

Revision ID: 20260329_05
Revises: 20260328_04
Create Date: 2026-03-29
"""
from alembic import op
import sqlalchemy as sa

revision = "20260329_05"
down_revision = "20260328_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "csf",
        sa.Column("pdf_content", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("csf", "pdf_content")
