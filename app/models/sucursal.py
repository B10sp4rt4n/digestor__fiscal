import uuid
from sqlalchemy import Column, String
from sqlalchemy.orm import relationship
from app.db.base import Base


class Sucursal(Base):
    __tablename__ = "sucursal"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    nombre = Column(String, nullable=False)
    geo = Column(String, nullable=True)
    serie_cfdi = Column(String, nullable=True)
    suc_hash = Column(String, unique=True, nullable=False)

    eventos = relationship("EventoFacturacion", back_populates="sucursal")
