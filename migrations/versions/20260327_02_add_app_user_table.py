"""add app_user table

Revision ID: 20260327_02
Revises: 20260327_01
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "20260327_02"
down_revision = "20260327_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_app_user_username", "app_user", ["username"])
    op.create_index("ix_app_user_tenant_id", "app_user", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_app_user_tenant_id", table_name="app_user")
    op.drop_index("ix_app_user_username", table_name="app_user")
    op.drop_table("app_user")
