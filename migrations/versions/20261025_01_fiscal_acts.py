"""Add local fiscal transition memory; preserve all prior records."""
from alembic import op
import sqlalchemy as sa

revision = "20261025_01"
down_revision = "20260405_05"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    names = {i["name"] for i in inspector.get_indexes("billing_draft")}
    names |= {i["name"] for i in inspector.get_unique_constraints("billing_draft")}
    if "uq_billing_draft_company_id" not in names:
        op.create_index("uq_billing_draft_company_id", "billing_draft", ["company_id", "id"], unique=True)
    if not inspector.has_table("billing_fiscal_acts"):
        op.create_table("billing_fiscal_acts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("draft_id", sa.String(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(), nullable=False),
            sa.Column("previous_hash", sa.String(), nullable=True),
            sa.Column("recorded_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["company_id", "draft_id"], ["billing_draft.company_id", "billing_draft.id"]),
            sa.UniqueConstraint("company_id", "draft_id", "sequence", name="uq_fiscal_act_sequence"))
    if bind.dialect.name == "postgresql":
        op.execute("""CREATE OR REPLACE FUNCTION refuse_fiscal_act_rewrite() RETURNS trigger
          LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Fiscal acts are append-only'; END; $$""")
        op.execute("""DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='fiscal_acts_append_only'
            AND tgrelid='billing_fiscal_acts'::regclass) THEN
            CREATE TRIGGER fiscal_acts_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
            ON billing_fiscal_acts FOR EACH STATEMENT EXECUTE FUNCTION refuse_fiscal_act_rewrite();
          END IF;
        END $$""")


def downgrade():
    raise RuntimeError("Fiscal history must be preserved; review forward recovery instead of dropping it")
