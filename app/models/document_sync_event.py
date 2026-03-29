import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from app.db.base import Base


class DocumentSyncEvent(Base):
    __tablename__ = "document_sync_events"
    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_doc_sync_company_idem"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    idempotency_key = Column(String, nullable=False)
    contract_version = Column(String, nullable=False, default="v1.0")
    event_type = Column(String, nullable=False, index=True)
    event_id = Column(String, nullable=False, unique=True, index=True)
    document_id = Column(String, nullable=False, index=True)
    delivery_status = Column(String, nullable=False, default="queued", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True, index=True)
    delivered_at = Column(DateTime, nullable=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)