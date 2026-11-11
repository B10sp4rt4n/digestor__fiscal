"""Add opt-in SAT read schedules; no backfill or fiscal state changes."""
from alembic import op
import sqlalchemy as sa

revision = '20261110_01'
down_revision = '20261107_02'
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table('sat_tracking_jobs'): return
    op.create_table('sat_tracking_jobs',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('company_id', sa.String(), nullable=False), sa.Column('draft_id', sa.String(), nullable=False),
        sa.Column('payment_id', sa.String()), sa.Column('document_uuid', sa.String(), nullable=False),
        sa.Column('document_hash', sa.String(), nullable=False), sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('enabled_by', sa.String(), nullable=False), sa.Column('next_check_at', sa.DateTime(), nullable=False),
        sa.Column('failures', sa.Integer(), nullable=False), sa.Column('lease_token', sa.String()),
        sa.Column('lease_until', sa.DateTime()), sa.Column('last_outcome', sa.String()),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['company_id', 'draft_id'], ['billing_draft.company_id', 'billing_draft.id']),
        sa.ForeignKeyConstraint(['company_id', 'payment_id'], ['billing_payments.company_id', 'billing_payments.id']),
        sa.UniqueConstraint('company_id', 'document_uuid', name='uq_sat_tracking_document'))
    op.create_index('ix_sat_tracking_due', 'sat_tracking_jobs', ['enabled', 'next_check_at'])


def downgrade():
    raise RuntimeError('Disable SAT_TRACKING_AUTOPOLL to roll back without discarding schedules or evidence')
