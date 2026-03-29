"""add csf metadata fields (bootstrap base tables if missing)

Revision ID: 20260327_01
Revises:
Create Date: 2026-03-27 05:15:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision: str = "20260327_01"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return set(inspector.get_table_names())


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    tables = _existing_tables()

    # --- Crear tablas base si la BD está vacía (Neon / Postgres limpio) ---
    if "csf" not in tables:
        op.create_table(
            "csf",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("rfc", sa.String(), nullable=False),
            sa.Column("razon_social", sa.String(), nullable=False),
            sa.Column("regimen", sa.String(), nullable=True),
            sa.Column("cp", sa.String(), nullable=True),
            sa.Column("curp", sa.String(), nullable=True),
            sa.Column("id_cif", sa.String(), nullable=True),
            sa.Column("source_filename", sa.String(), nullable=True),
            sa.Column("extracted_text", sa.Text(), nullable=True),
            sa.Column("qr_text", sa.String(), nullable=True),
            sa.Column("qr_valid", sa.Boolean(), nullable=True),
            sa.Column("qr_online", sa.Boolean(), nullable=True),
            sa.Column("uploaded_at", sa.DateTime(), nullable=True),
            sa.Column("issued_at", sa.DateTime(), nullable=True),
            sa.Column("csf_hash", sa.String(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("csf_hash"),
        )
        op.create_index("ix_csf_company_id", "csf", ["company_id"])

    if "sucursal" not in tables:
        op.create_table(
            "sucursal",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("nombre", sa.String(), nullable=False),
            sa.Column("geo", sa.String(), nullable=True),
            sa.Column("serie_cfdi", sa.String(), nullable=True),
            sa.Column("suc_hash", sa.String(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("suc_hash"),
        )
        op.create_index("ix_sucursal_company_id", "sucursal", ["company_id"])

    if "usuario" not in tables:
        op.create_table(
            "usuario",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("rol", sa.String(), nullable=False),
            sa.Column("device_id", sa.String(), nullable=True),
            sa.Column("usr_hash", sa.String(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("usr_hash"),
        )
        op.create_index("ix_usuario_company_id", "usuario", ["company_id"])

    if "evento_facturacion" not in tables:
        op.create_table(
            "evento_facturacion",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("csf_id", sa.String(), sa.ForeignKey("csf.id"), nullable=True),
            sa.Column("sucursal_id", sa.String(), sa.ForeignKey("sucursal.id"), nullable=True),
            sa.Column("usuario_id", sa.String(), sa.ForeignKey("usuario.id"), nullable=True),
            sa.Column("subtotal", sa.Float(), nullable=False, server_default="0"),
            sa.Column("impuestos", sa.Float(), nullable=False, server_default="0"),
            sa.Column("total", sa.Float(), nullable=False, server_default="0"),
            sa.Column("metodo_pago", sa.String(), nullable=True),
            sa.Column("forma_pago", sa.String(), nullable=True),
            sa.Column("timestamp", sa.DateTime(), nullable=True),
            sa.Column("evt_hash", sa.String(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("evt_hash"),
        )
        op.create_index("ix_evento_company_id", "evento_facturacion", ["company_id"])
        op.create_index("ix_evento_timestamp", "evento_facturacion", ["timestamp"])

    # --- Agregar columnas si la tabla csf ya existía (SQLite existente) ---
    existing = _existing_columns("csf")
    for col_name, col_def in [
        ("id_cif", sa.Column("id_cif", sa.String(), nullable=True)),
        ("source_filename", sa.Column("source_filename", sa.String(), nullable=True)),
        ("extracted_text", sa.Column("extracted_text", sa.Text(), nullable=True)),
        ("qr_text", sa.Column("qr_text", sa.String(), nullable=True)),
        ("qr_valid", sa.Column("qr_valid", sa.Boolean(), nullable=True)),
        ("qr_online", sa.Column("qr_online", sa.Boolean(), nullable=True)),
        ("uploaded_at", sa.Column("uploaded_at", sa.DateTime(), nullable=True)),
    ]:
        if col_name not in existing:
            op.add_column("csf", col_def)


def downgrade() -> None:
    existing = _existing_columns("csf")
    for col_name in ("uploaded_at", "qr_online", "qr_valid", "qr_text",
                     "extracted_text", "source_filename", "id_cif"):
        if col_name in existing:
            op.drop_column("csf", col_name)
