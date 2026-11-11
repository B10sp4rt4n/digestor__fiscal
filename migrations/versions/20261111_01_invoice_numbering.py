"""Numeración opt-in sin renumerar documentos existentes."""
from alembic import op
import sqlalchemy as sa
from app.models.invoice_numbering import InvoiceNumbering, InvoiceFolioReservation, InvoiceNumberingEvent

revision = '20261111_01'
down_revision = '20261107_02'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if 'numbering_managed' not in {column['name'] for column in sa.inspect(bind).get_columns('billing_draft')}:
        op.add_column('billing_draft', sa.Column('numbering_managed', sa.Boolean(), nullable=False, server_default=sa.false()))
    for model in (InvoiceNumbering, InvoiceFolioReservation, InvoiceNumberingEvent):
        model.__table__.create(bind, checkfirst=True)
    if bind.dialect.name == 'postgresql':
        op.execute("""CREATE OR REPLACE FUNCTION refuse_invoice_numbering_rewrite() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Invoice numbering evidence is append-only'; END; $$""")
        for table in ('invoice_folio_reservations', 'invoice_numbering_events'):
            op.execute(f"""DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='{table}_append_only'
                AND tgrelid='{table}'::regclass) THEN CREATE TRIGGER {table}_append_only
                BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT
                EXECUTE FUNCTION refuse_invoice_numbering_rewrite(); END IF; END $$""")


def downgrade():
    raise RuntimeError('Conservar folios reservados; utilizar una migración correctiva')
