"""Durable schedules for explicitly enrolled, tenant-bound SAT documents."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from app.db.base import Base


class SatTrackingJob(Base):
    __tablename__ = 'sat_tracking_jobs'
    __table_args__ = (
        ForeignKeyConstraint(['company_id', 'draft_id'], ['billing_draft.company_id', 'billing_draft.id']),
        ForeignKeyConstraint(['company_id', 'payment_id'], ['billing_payments.company_id', 'billing_payments.id']),
        UniqueConstraint('company_id', 'document_uuid', name='uq_sat_tracking_document'),
        Index('ix_sat_tracking_due', 'enabled', 'next_check_at'),
    )
    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False)
    draft_id = Column(String, nullable=False)
    payment_id = Column(String)
    document_uuid = Column(String, nullable=False)
    document_hash = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False)
    enabled_by = Column(String, nullable=False)
    next_check_at = Column(DateTime, nullable=False)  # UTC
    failures = Column(Integer, nullable=False, default=0)
    lease_token = Column(String)
    lease_until = Column(DateTime)
    last_outcome = Column(String)
    updated_at = Column(DateTime, nullable=False)
