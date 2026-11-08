"""Independent cancellation intent, outcome and append-only evidence. No backfill."""
from alembic import op
import sqlalchemy as sa

revision = '20261107_01'
down_revision = '20261025_02'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table('billing_cancellations'):
        op.create_table('billing_cancellations',
            sa.Column('id', sa.String(), primary_key=True), sa.Column('company_id', sa.String(), nullable=False),
            sa.Column('draft_id', sa.String(), nullable=False), sa.Column('payment_id', sa.String()),
            sa.Column('document_uuid', sa.String(), nullable=False), sa.Column('document_hash', sa.String(), nullable=False),
            sa.Column('idempotency_key', sa.String(), nullable=False), sa.Column('intent', sa.Text(), nullable=False),
            sa.Column('status', sa.String(), nullable=False), sa.Column('acknowledgement_xml', sa.Text()),
            sa.Column('provider_response', sa.Text()), sa.Column('checked_at', sa.DateTime()),
            sa.ForeignKeyConstraint(['company_id', 'draft_id'], ['billing_draft.company_id', 'billing_draft.id']),
            sa.ForeignKeyConstraint(['company_id', 'payment_id'], ['billing_payments.company_id', 'billing_payments.id']),
            sa.UniqueConstraint('company_id', 'id', name='uq_cancellation_company_id'),
            sa.UniqueConstraint('company_id', 'document_uuid', name='uq_cancellation_document'),
            sa.UniqueConstraint('company_id', 'idempotency_key', name='uq_cancellation_key'))
    if not sa.inspect(bind).has_table('billing_cancellation_acts'):
        op.create_table('billing_cancellation_acts',
            sa.Column('id', sa.String(), primary_key=True), sa.Column('company_id', sa.String(), nullable=False),
            sa.Column('cancellation_id', sa.String(), nullable=False), sa.Column('sequence', sa.Integer(), nullable=False),
            sa.Column('payload', sa.Text(), nullable=False), sa.Column('content_hash', sa.String(), nullable=False),
            sa.ForeignKeyConstraint(['company_id', 'cancellation_id'], ['billing_cancellations.company_id', 'billing_cancellations.id']),
            sa.UniqueConstraint('company_id', 'cancellation_id', 'sequence', name='uq_cancellation_act_sequence'))
    if bind.dialect.name == 'postgresql':
        op.execute("""CREATE OR REPLACE FUNCTION refuse_cancellation_act_rewrite() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Cancellation evidence is append-only'; END; $$""")
        op.execute("""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='cancellation_acts_append_only'
                           AND tgrelid='billing_cancellation_acts'::regclass) THEN
                CREATE TRIGGER cancellation_acts_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
                ON billing_cancellation_acts FOR EACH STATEMENT EXECUTE FUNCTION refuse_cancellation_act_rewrite();
            END IF; END $$""")


def downgrade():
    raise RuntimeError('Preserve cancellation evidence; use forward recovery')
