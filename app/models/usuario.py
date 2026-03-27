import uuid
from sqlalchemy import Column, String
from sqlalchemy.orm import relationship
from app.db.base import Base


class Usuario(Base):
    __tablename__ = "usuario"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    rol = Column(String, nullable=False)
    device_id = Column(String, nullable=True)
    usr_hash = Column(String, unique=True, nullable=False)

    eventos = relationship("EventoFacturacion", back_populates="usuario")
