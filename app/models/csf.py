import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, String, Integer, DateTime, Text
from sqlalchemy.orm import relationship
from app.db.base import Base


class CSF(Base):
    __tablename__ = "csf"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    rfc = Column(String, nullable=False)
    razon_social = Column(String, nullable=False)
    regimen = Column(String, nullable=True)
    cp = Column(String, nullable=True)
    curp = Column(String, nullable=True)
    id_cif = Column(String, nullable=True)
    source_filename = Column(String, nullable=True)
    extracted_text = Column(Text, nullable=True)
    qr_text = Column(String, nullable=True)
    qr_valid = Column(Boolean, nullable=True)
    qr_online = Column(Boolean, nullable=True)
    parser_source = Column(String, nullable=False, default="regex", server_default="regex")
    processing_status = Column(String, nullable=False, default="processed", server_default="processed")
    status_reason = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    issued_at = Column(DateTime, default=datetime.utcnow)
    csf_hash = Column(String, unique=True, nullable=False)
    version = Column(Integer, default=1)

    eventos = relationship("EventoFacturacion", back_populates="csf")
