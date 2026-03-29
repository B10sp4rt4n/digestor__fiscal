import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, Integer
from app.db.base import Base


class DocumentJob(Base):
    __tablename__ = "document_jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    document_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")  # queued, processing, done, failed
    document_id = Column(String, nullable=True, index=True)  # CSF id si ya existe
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    processing_time_ms = Column(Integer, nullable=True)
