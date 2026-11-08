"""Preserve fiscal substitution requests/results and immutable acts. No backfill."""
from alembic import op
import sqlalchemy as sa

revision = '20261107_02'
down_revision = '20261107_01'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table('billing_fiscal_revisions'):
        op.create_table('billing_fiscal_revisions',
            sa.Column('id', sa.String(), primary_key=True), sa.Column('company_id', sa.String(), nullable=False),
            sa.Column('draft_id', sa.String(), nullable=False), sa.Column('payment_id', sa.String()),
            sa.Column('source_uuid', sa.String(), nullable=False), sa.Column('source_xml', sa.Text(), nullable=False),
            sa.Column('declaration', sa.Text(), nullable=False), sa.Column('idempotency_key', sa.String(), nullable=False),
            sa.Column('request_xml', sa.Text(), nullable=False), sa.Column('status', sa.String(), nullable=False),
            sa.Column('result_uuid', sa.String()), sa.Column('result_xml', sa.Text()),
            sa.ForeignKeyConstraint(['company_id', 'draft_id'], ['billing_draft.company_id', 'billing_draft.id']),
            sa.ForeignKeyConstraint(['company_id', 'payment_id'], ['billing_payments.company_id', 'billing_payments.id']),
            sa.UniqueConstraint('company_id', 'id', name='uq_fiscal_revision_company_id'),
            sa.UniqueConstraint('company_id', 'idempotency_key', name='uq_fiscal_revision_key'),
            sa.UniqueConstraint('company_id', 'result_uuid', name='uq_fiscal_revision_result'))
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_fiscal_revision_source ON billing_fiscal_revisions (company_id, source_uuid) WHERE status <> 'abandoned'")
    if not sa.inspect(bind).has_table('billing_fiscal_revision_acts'):
        op.create_table('billing_fiscal_revision_acts',
            sa.Column('id', sa.String(), primary_key=True), sa.Column('company_id', sa.String(), nullable=False),
            sa.Column('revision_id', sa.String(), nullable=False), sa.Column('sequence', sa.Integer(), nullable=False),
            sa.Column('payload', sa.Text(), nullable=False), sa.Column('content_hash', sa.String(), nullable=False),
            sa.ForeignKeyConstraint(['company_id', 'revision_id'], ['billing_fiscal_revisions.company_id', 'billing_fiscal_revisions.id']),
            sa.UniqueConstraint('company_id', 'revision_id', 'sequence', name='uq_fiscal_revision_act_sequence'))
    if bind.dialect.name == 'postgresql':
        op.execute("""CREATE OR REPLACE FUNCTION refuse_fiscal_revision_act_rewrite() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Fiscal correction evidence is append-only'; END; $$""")
        op.execute("""DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='fiscal_revision_acts_append_only'
            AND tgrelid='billing_fiscal_revision_acts'::regclass) THEN
            CREATE TRIGGER fiscal_revision_acts_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
            ON billing_fiscal_revision_acts FOR EACH STATEMENT EXECUTE FUNCTION refuse_fiscal_revision_act_rewrite();
            END IF; END $$""")


def downgrade():
    raise RuntimeError('Preserve fiscal corrections; use forward recovery')
