"""add audit_logs table with immutable chain hashing

Revision ID: 20260330_07
Revises: 20260330_06
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa


revision = "20260330_07"
down_revision = "20260330_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not op.get_context().connection.dialect.has_table(op.get_context().connection, "audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("entity_type", sa.String(), nullable=False),
            sa.Column("entity_id", sa.String(), nullable=False),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("previous_hash", sa.String(), nullable=True),
            sa.Column("content_hash", sa.String(), nullable=False),
            sa.Column("chain_hash", sa.String(), nullable=False),
            sa.Column("ip_address", sa.String(), nullable=True),
            sa.Column("user_agent", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="success"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("sequence_number", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_audit_logs_company_id", "audit_logs", ["company_id"])
        op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
        op.create_index("ix_audit_logs_chain_hash", "audit_logs", ["chain_hash"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_chain_hash", "audit_logs")
    op.drop_index("ix_audit_logs_entity_id", "audit_logs")
    op.drop_index("ix_audit_logs_company_id", "audit_logs")
    op.drop_table("audit_logs")
