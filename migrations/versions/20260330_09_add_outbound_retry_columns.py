"""add retry/delivery columns to document_sync_events

Revision ID: 20260330_09
Revises: 20260330_08
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260330_09"
down_revision = "20260330_08"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    columns = inspector.get_columns(table_name)
    return any(col.get("name") == column_name for col in columns)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("document_sync_events"):
        return

    if not _has_column(inspector, "document_sync_events", "last_attempt_at"):
        op.add_column("document_sync_events", sa.Column("last_attempt_at", sa.DateTime(), nullable=True))

    if not _has_column(inspector, "document_sync_events", "next_retry_at"):
        op.add_column("document_sync_events", sa.Column("next_retry_at", sa.DateTime(), nullable=True))

    if not _has_column(inspector, "document_sync_events", "delivered_at"):
        op.add_column("document_sync_events", sa.Column("delivered_at", sa.DateTime(), nullable=True))

    indexes = {idx["name"] for idx in inspector.get_indexes("document_sync_events")}
    index_name = op.f("ix_document_sync_events_next_retry_at")
    if index_name not in indexes:
        op.create_index(index_name, "document_sync_events", ["next_retry_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("document_sync_events"):
        return

    indexes = {idx["name"] for idx in inspector.get_indexes("document_sync_events")}
    index_name = op.f("ix_document_sync_events_next_retry_at")
    if index_name in indexes:
        op.drop_index(index_name, table_name="document_sync_events")

    if _has_column(inspector, "document_sync_events", "delivered_at"):
        op.drop_column("document_sync_events", "delivered_at")
    if _has_column(inspector, "document_sync_events", "next_retry_at"):
        op.drop_column("document_sync_events", "next_retry_at")
    if _has_column(inspector, "document_sync_events", "last_attempt_at"):
        op.drop_column("document_sync_events", "last_attempt_at")
