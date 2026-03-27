import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base


class EventoFacturacion(Base):
    __tablename__ = "evento_facturacion"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    csf_id = Column(String, ForeignKey("csf.id"), nullable=True)
    sucursal_id = Column(String, ForeignKey("sucursal.id"), nullable=True)
    usuario_id = Column(String, ForeignKey("usuario.id"), nullable=True)
    subtotal = Column(Float, nullable=False, default=0.0)
    impuestos = Column(Float, nullable=False, default=0.0)
    total = Column(Float, nullable=False, default=0.0)
    metodo_pago = Column(String, nullable=True)
    forma_pago = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    evt_hash = Column(String, unique=True, nullable=False)

    csf = relationship("CSF", back_populates="eventos")
    sucursal = relationship("Sucursal", back_populates="eventos")
    usuario = relationship("Usuario", back_populates="eventos")
