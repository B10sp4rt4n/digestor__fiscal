"""add commercial catalog and billing drafts

Revision ID: 20260405_03
Revises: 20260330_09
Create Date: 2026-04-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260405_03"
down_revision: Union[str, None] = "20260330_09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return set(inspector.get_table_names())


def upgrade() -> None:
    tables = _existing_tables()

    if "product_catalog" not in tables:
        op.create_table(
            "product_catalog",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("sku", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("sat_product_code", sa.String(), nullable=False, server_default="78101800"),
            sa.Column("unit_code", sa.String(), nullable=False, server_default="E48"),
            sa.Column("unit_name", sa.String(), nullable=True),
            sa.Column("price", sa.Float(), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(), nullable=False, server_default="MXN"),
            sa.Column("tax_rate", sa.Float(), nullable=False, server_default="0.16"),
            sa.Column("tax_object", sa.String(), nullable=False, server_default="02"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("company_id", "sku", name="uq_product_catalog_company_sku"),
        )
        op.create_index("ix_product_catalog_company_id", "product_catalog", ["company_id"])

    if "billing_draft" not in tables:
        op.create_table(
            "billing_draft",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="draft"),
            sa.Column("customer_name", sa.String(), nullable=True),
            sa.Column("customer_rfc", sa.String(), nullable=True),
            sa.Column("customer_zip", sa.String(), nullable=True),
            sa.Column("customer_regimen", sa.String(), nullable=True),
            sa.Column("customer_use_cfdi", sa.String(), nullable=True),
            sa.Column("emitter_rfc", sa.String(), nullable=False, server_default="IIA040805DZ4"),
            sa.Column("emitter_name", sa.String(), nullable=False, server_default="INDISTRIA ILUMINADORA DE ALMACENES"),
            sa.Column("emitter_regimen", sa.String(), nullable=False, server_default="626"),
            sa.Column("place_of_issue", sa.String(), nullable=False, server_default="32690"),
            sa.Column("currency", sa.String(), nullable=False, server_default="MXN"),
            sa.Column("payment_method", sa.String(), nullable=False, server_default="PUE"),
            sa.Column("payment_form", sa.String(), nullable=False, server_default="01"),
            sa.Column("series", sa.String(), nullable=True),
            sa.Column("folio", sa.String(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("subtotal", sa.Float(), nullable=False, server_default="0"),
            sa.Column("taxes", sa.Float(), nullable=False, server_default="0"),
            sa.Column("total", sa.Float(), nullable=False, server_default="0"),
            sa.Column("stamped_at", sa.DateTime(), nullable=True),
            sa.Column("stamp_status", sa.String(), nullable=False, server_default="not_sent"),
            sa.Column("stamped_xml_base64", sa.Text(), nullable=True),
            sa.Column("stamped_response_json", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_billing_draft_company_id", "billing_draft", ["company_id"])
        op.create_index("ix_billing_draft_status", "billing_draft", ["status"])

    if "billing_draft_item" not in tables:
        op.create_table(
            "billing_draft_item",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("draft_id", sa.String(), sa.ForeignKey("billing_draft.id", ondelete="CASCADE"), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("product_id", sa.String(), sa.ForeignKey("product_catalog.id"), nullable=True),
            sa.Column("sku", sa.String(), nullable=True),
            sa.Column("description", sa.String(), nullable=False),
            sa.Column("sat_product_code", sa.String(), nullable=False, server_default="78101800"),
            sa.Column("unit_code", sa.String(), nullable=False, server_default="E48"),
            sa.Column("quantity", sa.Float(), nullable=False, server_default="1"),
            sa.Column("unit_price", sa.Float(), nullable=False, server_default="0"),
            sa.Column("tax_rate", sa.Float(), nullable=False, server_default="0.16"),
            sa.Column("tax_object", sa.String(), nullable=False, server_default="02"),
            sa.Column("line_subtotal", sa.Float(), nullable=False, server_default="0"),
            sa.Column("line_taxes", sa.Float(), nullable=False, server_default="0"),
            sa.Column("line_total", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_billing_draft_item_draft_id", "billing_draft_item", ["draft_id"])
        op.create_index("ix_billing_draft_item_company_id", "billing_draft_item", ["company_id"])


def downgrade() -> None:
    tables = _existing_tables()

    if "billing_draft_item" in tables:
        op.drop_index("ix_billing_draft_item_company_id", table_name="billing_draft_item")
        op.drop_index("ix_billing_draft_item_draft_id", table_name="billing_draft_item")
        op.drop_table("billing_draft_item")

    if "billing_draft" in tables:
        op.drop_index("ix_billing_draft_status", table_name="billing_draft")
        op.drop_index("ix_billing_draft_company_id", table_name="billing_draft")
        op.drop_table("billing_draft")

    if "product_catalog" in tables:
        op.drop_index("ix_product_catalog_company_id", table_name="product_catalog")
        op.drop_table("product_catalog")
