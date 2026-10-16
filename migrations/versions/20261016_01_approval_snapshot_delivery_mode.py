"""Add approval evidence and explicit delivery mode; no legacy evidence backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '20261016_01'
down_revision = '20260405_05'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('csf', sa.Column('approval_id', sa.String(), nullable=True))
    op.add_column('csf', sa.Column('approved_at', sa.DateTime(), nullable=True))
    op.add_column('csf', sa.Column('approved_by', sa.String(), nullable=True))
    op.add_column('csf', sa.Column('approval_snapshot', JSONB(), nullable=True))
    op.add_column('document_sync_events', sa.Column('delivery_mode', sa.String(), nullable=True))


def downgrade():
    op.drop_column('document_sync_events', 'delivery_mode')
    for name in ('approval_snapshot', 'approved_by', 'approved_at', 'approval_id'):
        op.drop_column('csf', name)
