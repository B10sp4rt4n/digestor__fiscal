import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, Integer, LargeBinary
from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False)
    action = Column(String, nullable=False)  # upload, process, export, validate, etc.
    entity_type = Column(String, nullable=False)  # document_job, csf, user, etc.
    entity_id = Column(String, nullable=False, index=True)
    
    # Contenido (JSON serializado)
    details = Column(Text, nullable=True)  # JSON con cambios: {before, after, metadata}
    
    # Cadena inmutable de hashes
    previous_hash = Column(String, nullable=True)  # Hash del audit_log anterior en la cadena
    content_hash = Column(String, nullable=False)  # SHA256 del contenido de esto (accion+entity+details)
    chain_hash = Column(String, nullable=False, index=True)  # SHA256(previous_hash + content_hash)
    
    # Metadatos de la solicitud
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    status = Column(String, nullable=False, default="success")  # success, error
    error_message = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    sequence_number = Column(Integer, nullable=False)  # Número secuencial por company_id para order
