"""add document_sync_events table for outbound idempotency

Revision ID: 20260330_08
Revises: 20260330_07
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260330_08"
down_revision = "20260330_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("document_sync_events"):
        op.create_table(
            "document_sync_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("idempotency_key", sa.String(), nullable=False),
            sa.Column("contract_version", sa.String(), nullable=False, server_default="v1.0"),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("event_id", sa.String(), nullable=False),
            sa.Column("document_id", sa.String(), nullable=False),
            sa.Column("delivery_status", sa.String(), nullable=False, server_default="queued"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("company_id", "idempotency_key", name="uq_doc_sync_company_idem"),
            sa.UniqueConstraint("event_id"),
        )

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("document_sync_events")}
    desired_indexes = {
        op.f("ix_document_sync_events_company_id"): ["company_id"],
        op.f("ix_document_sync_events_event_id"): ["event_id"],
        op.f("ix_document_sync_events_document_id"): ["document_id"],
        op.f("ix_document_sync_events_event_type"): ["event_type"],
        op.f("ix_document_sync_events_delivery_status"): ["delivery_status"],
    }
    for name, cols in desired_indexes.items():
        if name not in existing_indexes:
            op.create_index(name, "document_sync_events", cols, unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_document_sync_events_delivery_status"), table_name="document_sync_events")
    op.drop_index(op.f("ix_document_sync_events_event_type"), table_name="document_sync_events")
    op.drop_index(op.f("ix_document_sync_events_document_id"), table_name="document_sync_events")
    op.drop_index(op.f("ix_document_sync_events_event_id"), table_name="document_sync_events")
    op.drop_index(op.f("ix_document_sync_events_company_id"), table_name="document_sync_events")
    op.drop_table("document_sync_events")
