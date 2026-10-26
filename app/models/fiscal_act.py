"""Local append-only fiscal memory. Hashes are integrity checks, not signatures."""
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKeyConstraint, UniqueConstraint, event
from app.db.base import Base


class FiscalAct(Base):
    __tablename__ = "billing_fiscal_acts"
    __table_args__ = (
        ForeignKeyConstraint(["company_id", "draft_id"], ["billing_draft.company_id", "billing_draft.id"]),
        UniqueConstraint("company_id", "draft_id", "sequence", name="uq_fiscal_act_sequence"),
    )
    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False)
    draft_id = Column(String, nullable=False)
    sequence = Column(Integer, nullable=False)
    payload = Column(Text, nullable=False)
    content_hash = Column(String, nullable=False)
    previous_hash = Column(String, nullable=True)
    recorded_at = Column(DateTime, nullable=False)


@event.listens_for(FiscalAct, "before_update")
@event.listens_for(FiscalAct, "before_delete")
def refuse_rewrite(*args):
    raise ValueError("Fiscal acts are append-only; record a correction instead")
