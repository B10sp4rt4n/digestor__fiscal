"""add document_jobs table

Revision ID: 20260330_06
Revises: 20260329_05
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa


revision = "20260330_06"
down_revision = "20260329_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if table already exists (idempotent)
    if not op.get_context().connection.dialect.has_table(op.get_context().connection, "document_jobs"):
        op.create_table(
            "document_jobs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("document_type", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="queued"),
            sa.Column("document_id", sa.String(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("processing_time_ms", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_document_jobs_company_id", "document_jobs", ["company_id"])
        op.create_index("ix_document_jobs_document_id", "document_jobs", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_jobs_document_id", "document_jobs")
    op.drop_index("ix_document_jobs_company_id", "document_jobs")
    op.drop_table("document_jobs")
