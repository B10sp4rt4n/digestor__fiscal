"""Cancellation is independent of stamp success and economic payment history."""
from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKeyConstraint, UniqueConstraint, event
from app.db.base import Base


class BillingCancellation(Base):
    __tablename__ = 'billing_cancellations'
    __table_args__ = (
        ForeignKeyConstraint(['company_id', 'draft_id'], ['billing_draft.company_id', 'billing_draft.id']),
        ForeignKeyConstraint(['company_id', 'payment_id'], ['billing_payments.company_id', 'billing_payments.id']),
        UniqueConstraint('company_id', 'id', name='uq_cancellation_company_id'),
        UniqueConstraint('company_id', 'document_uuid', name='uq_cancellation_document'),
        UniqueConstraint('company_id', 'idempotency_key', name='uq_cancellation_key'),
    )
    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False)
    draft_id = Column(String, nullable=False)
    payment_id = Column(String)
    document_uuid = Column(String, nullable=False)
    document_hash = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    intent = Column(Text, nullable=False)
    status = Column(String, nullable=False)
    acknowledgement_xml = Column(Text)
    provider_response = Column(Text)
    checked_at = Column(DateTime)


class CancellationAct(Base):
    __tablename__ = 'billing_cancellation_acts'
    __table_args__ = (
        ForeignKeyConstraint(['company_id', 'cancellation_id'], ['billing_cancellations.company_id', 'billing_cancellations.id']),
        UniqueConstraint('company_id', 'cancellation_id', 'sequence', name='uq_cancellation_act_sequence'),
    )
    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False)
    cancellation_id = Column(String, nullable=False)
    sequence = Column(Integer, nullable=False)
    payload = Column(Text, nullable=False)
    content_hash = Column(String, nullable=False)


@event.listens_for(CancellationAct, 'before_update')
@event.listens_for(CancellationAct, 'before_delete')
def prevent_rewrite(*args):
    raise ValueError('Cancellation evidence is append-only')
