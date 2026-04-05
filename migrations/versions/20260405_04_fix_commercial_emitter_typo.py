"""fix commercial emitter typo in existing drafts

Revision ID: 20260405_04
Revises: 20260405_03
Create Date: 2026-04-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260405_04"
down_revision: Union[str, None] = "20260405_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD = "INDISTRIA ILUMINADORA DE ALMACENES"
_NEW = "INDUSTRIA ILUMINADORA DE ALMACENES"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE billing_draft SET emitter_name = :new WHERE emitter_name = :old"
        ).bindparams(old=_OLD, new=_NEW)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE billing_draft SET emitter_name = :old WHERE emitter_name = :new"
        ).bindparams(old=_OLD, new=_NEW)
    )
