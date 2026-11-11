"""Numeración interna por empresa; reservas y cambios se conservan."""
import uuid
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from app.db.base import Base


class InvoiceNumbering(Base):
    __tablename__ = 'invoice_numbering'
    __table_args__ = (
        CheckConstraint('digits BETWEEN 1 AND 12', name='ck_invoice_numbering_digits'),
        CheckConstraint('next_number BETWEEN 1 AND 1000000000000', name='ck_invoice_numbering_next'),
        CheckConstraint('revision > 0', name='ck_invoice_numbering_revision'),
        CheckConstraint('NOT reset_annually OR include_year', name='ck_invoice_numbering_annual'),
    )
    company_id = Column(String, primary_key=True)
    prefix = Column(String(25), nullable=False)
    include_year = Column(Boolean, nullable=False)
    reset_annually = Column(Boolean, nullable=False)
    digits = Column(Integer, nullable=False)
    next_number = Column(BigInteger, nullable=False)
    period_year = Column(Integer, nullable=False)
    revision = Column(Integer, nullable=False)


class InvoiceFolioReservation(Base):
    __tablename__ = 'invoice_folio_reservations'
    __table_args__ = (
        ForeignKeyConstraint(['company_id', 'draft_id'], ['billing_draft.company_id', 'billing_draft.id']),
        UniqueConstraint('company_id', 'series', 'folio', name='uq_invoice_folio_company_series'),
    )
    draft_id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False, index=True)
    series = Column(String(25), nullable=False)
    folio = Column(String(40), nullable=False)
    number = Column(BigInteger, nullable=False)
    policy_revision = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class InvoiceNumberingEvent(Base):
    __tablename__ = 'invoice_numbering_events'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    authenticated_actor = Column(String, nullable=False)
    declared_actor = Column(String, nullable=True)
    details = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
