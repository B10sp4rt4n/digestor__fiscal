"""Add payment declarations, receipts and append-only evidence; no backfill."""
from alembic import op
import sqlalchemy as sa

revision = '20261025_02'
down_revision = '20261025_01'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('billing_payments'):
        op.create_table('billing_payments',
            sa.Column('id',sa.String(),primary_key=True),
            sa.Column('company_id',sa.String(),nullable=False),sa.Column('draft_id',sa.String(),nullable=False),
            sa.Column('sequence',sa.Integer(),nullable=False),sa.Column('idempotency_key',sa.String(),nullable=False),
            sa.Column('reference',sa.String(),nullable=False),sa.Column('paid_at',sa.DateTime(),nullable=False),
            sa.Column('payment_form',sa.String(),nullable=False),sa.Column('amount',sa.Numeric(14,2),nullable=False),
            sa.Column('balance_before',sa.Numeric(14,2),nullable=False),sa.Column('balance_after',sa.Numeric(14,2),nullable=False),
            sa.Column('tax_base',sa.Numeric(18,6),nullable=False),sa.Column('tax_amount',sa.Numeric(18,6),nullable=False),
            sa.Column('invoice_hash',sa.String(),nullable=False),sa.Column('declaration',sa.Text(),nullable=False),
            sa.Column('declaration_hash',sa.String(),nullable=False),sa.Column('created_at',sa.DateTime(),nullable=False),
            sa.ForeignKeyConstraint(['company_id','draft_id'],['billing_draft.company_id','billing_draft.id']),
            sa.UniqueConstraint('company_id','id',name='uq_billing_payment_company_id'),
            sa.UniqueConstraint('company_id','draft_id','sequence',name='uq_billing_payment_sequence'),
            sa.UniqueConstraint('company_id','idempotency_key',name='uq_billing_payment_key'),
            sa.UniqueConstraint('company_id','reference',name='uq_billing_payment_reference'),
            sa.CheckConstraint('amount > 0 AND balance_after >= 0 AND round(balance_before - amount - balance_after, 2) = 0',name='ck_billing_payment_balance'))
    if not inspector.has_table('billing_payment_receipts'):
        op.create_table('billing_payment_receipts',
            sa.Column('payment_id',sa.String(),primary_key=True),sa.Column('company_id',sa.String(),nullable=False),
            sa.Column('status',sa.String(),nullable=False),sa.Column('request_xml',sa.Text()),
            sa.Column('xml_base64',sa.Text()),sa.Column('uuid',sa.String()),sa.Column('provider_response',sa.Text()),
            sa.Column('stamped_at',sa.DateTime()),
            sa.ForeignKeyConstraint(['company_id','payment_id'],['billing_payments.company_id','billing_payments.id']))
    if not inspector.has_table('billing_payment_acts'):
        op.create_table('billing_payment_acts',
            sa.Column('id',sa.String(),primary_key=True),sa.Column('company_id',sa.String(),nullable=False),
            sa.Column('payment_id',sa.String(),nullable=False),sa.Column('sequence',sa.Integer(),nullable=False),
            sa.Column('payload',sa.Text(),nullable=False),sa.Column('content_hash',sa.String(),nullable=False),
            sa.Column('previous_hash',sa.String()),sa.Column('recorded_at',sa.DateTime(),nullable=False),
            sa.ForeignKeyConstraint(['company_id','payment_id'],['billing_payments.company_id','billing_payments.id']),
            sa.UniqueConstraint('company_id','payment_id','sequence',name='uq_billing_payment_act_sequence'))
    if bind.dialect.name == 'postgresql':
        op.execute("""CREATE OR REPLACE FUNCTION refuse_payment_evidence_rewrite() RETURNS trigger
          LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Payment evidence is append-only'; END; $$""")
        for name in ('billing_payments','billing_payment_acts'):
            op.execute(sa.text(f"""DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='{name}_append_only' AND tgrelid='{name}'::regclass) THEN
                CREATE TRIGGER {name}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {name}
                FOR EACH STATEMENT EXECUTE FUNCTION refuse_payment_evidence_rewrite();
              END IF; END $$"""))


def downgrade():
    raise RuntimeError('Preserve payment declarations and fiscal evidence; use forward recovery')
