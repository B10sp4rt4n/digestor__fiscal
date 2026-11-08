"""Fiscal replacements preserve original invoices, receipts and economic payments."""
from sqlalchemy import Column, String, Text, Integer, ForeignKeyConstraint, UniqueConstraint, Index, text, event
from app.db.base import Base


class FiscalRevision(Base):
    __tablename__ = 'billing_fiscal_revisions'
    __table_args__ = (
        ForeignKeyConstraint(['company_id', 'draft_id'], ['billing_draft.company_id', 'billing_draft.id']),
        ForeignKeyConstraint(['company_id', 'payment_id'], ['billing_payments.company_id', 'billing_payments.id']),
        UniqueConstraint('company_id', 'id', name='uq_fiscal_revision_company_id'),
        Index('uq_fiscal_revision_source', 'company_id', 'source_uuid', unique=True,
            postgresql_where=text("status <> 'abandoned'"), sqlite_where=text("status <> 'abandoned'")),
        UniqueConstraint('company_id', 'idempotency_key', name='uq_fiscal_revision_key'),
        UniqueConstraint('company_id', 'result_uuid', name='uq_fiscal_revision_result'),
    )
    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False)
    draft_id = Column(String, nullable=False)
    payment_id = Column(String)
    source_uuid = Column(String, nullable=False)
    source_xml = Column(Text, nullable=False)
    declaration = Column(Text, nullable=False)
    idempotency_key = Column(String, nullable=False)
    request_xml = Column(Text, nullable=False)
    status = Column(String, nullable=False)
    result_uuid = Column(String)
    result_xml = Column(Text)


class FiscalRevisionAct(Base):
    __tablename__ = 'billing_fiscal_revision_acts'
    __table_args__ = (
        ForeignKeyConstraint(['company_id', 'revision_id'], ['billing_fiscal_revisions.company_id', 'billing_fiscal_revisions.id']),
        UniqueConstraint('company_id', 'revision_id', 'sequence', name='uq_fiscal_revision_act_sequence'),
    )
    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False)
    revision_id = Column(String, nullable=False)
    sequence = Column(Integer, nullable=False)
    payload = Column(Text, nullable=False)
    content_hash = Column(String, nullable=False)


@event.listens_for(FiscalRevisionAct, 'before_update')
@event.listens_for(FiscalRevisionAct, 'before_delete')
def prevent_rewrite(*args):
    raise ValueError('Fiscal correction evidence is append-only')
