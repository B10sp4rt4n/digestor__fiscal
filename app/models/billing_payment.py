"""Payment declarations are immutable; receipt status is a separate aggregate."""
from datetime import datetime
import uuid
from sqlalchemy import Column, String, Integer, Numeric, DateTime, Text, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, event
from app.db.base import Base


class BillingPayment(Base):
    __tablename__ = 'billing_payments'
    __table_args__ = (
        ForeignKeyConstraint(['company_id', 'draft_id'], ['billing_draft.company_id', 'billing_draft.id']),
        UniqueConstraint('company_id', 'id', name='uq_billing_payment_company_id'),
        UniqueConstraint('company_id', 'draft_id', 'sequence', name='uq_billing_payment_sequence'),
        UniqueConstraint('company_id', 'idempotency_key', name='uq_billing_payment_key'),
        UniqueConstraint('company_id', 'reference', name='uq_billing_payment_reference'),
        CheckConstraint('amount > 0 AND balance_after >= 0 AND round(balance_before - amount - balance_after, 2) = 0', name='ck_billing_payment_balance'),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False)
    draft_id = Column(String, nullable=False)
    sequence = Column(Integer, nullable=False)
    idempotency_key = Column(String, nullable=False)
    reference = Column(String, nullable=False)
    paid_at = Column(DateTime, nullable=False)  # UTC; original offset in declaration
    payment_form = Column(String, nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    balance_before = Column(Numeric(14, 2), nullable=False)
    balance_after = Column(Numeric(14, 2), nullable=False)
    tax_base = Column(Numeric(18, 6), nullable=False)
    tax_amount = Column(Numeric(18, 6), nullable=False)
    invoice_hash = Column(String, nullable=False)
    declaration = Column(Text, nullable=False)
    declaration_hash = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PaymentReceipt(Base):
    __tablename__ = 'billing_payment_receipts'
    __table_args__ = (ForeignKeyConstraint(['company_id', 'payment_id'], ['billing_payments.company_id', 'billing_payments.id']),)
    payment_id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default='not_sent')
    request_xml = Column(Text)
    xml_base64 = Column(Text)
    uuid = Column(String)
    provider_response = Column(Text)
    stamped_at = Column(DateTime)


class PaymentAct(Base):
    __tablename__ = 'billing_payment_acts'
    __table_args__ = (
        ForeignKeyConstraint(['company_id', 'payment_id'], ['billing_payments.company_id', 'billing_payments.id']),
        UniqueConstraint('company_id', 'payment_id', 'sequence', name='uq_billing_payment_act_sequence'),
    )
    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False)
    payment_id = Column(String, nullable=False)
    sequence = Column(Integer, nullable=False)
    payload = Column(Text, nullable=False)
    content_hash = Column(String, nullable=False)
    previous_hash = Column(String)
    recorded_at = Column(DateTime, nullable=False)


@event.listens_for(BillingPayment, 'before_update')
@event.listens_for(BillingPayment, 'before_delete')
@event.listens_for(PaymentAct, 'before_update')
@event.listens_for(PaymentAct, 'before_delete')
def prevent_rewrite(*args):
    raise ValueError('Payment declarations and acts are append-only')
