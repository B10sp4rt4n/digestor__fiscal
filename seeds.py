from app.db.session import SessionLocal, engine
from app.db.base import Base
from app.models.csf import CSF
from app.models.sucursal import Sucursal
from app.models.usuario import Usuario
from app.models.evento import EventoFacturacion
from datetime import datetime
import uuid

def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # CSF demo
        csf = CSF(
            company_id="demo-company",
            rfc="XAXX010101000",
            razon_social="Empresa Demo S.A. de C.V.",
            regimen="General de Ley Personas Morales",
            cp="01000",
            curp=None,
            issued_at=datetime.utcnow(),
            csf_hash="hash-demo-csf",
            version=1
        )
        db.add(csf)
        suc = Sucursal(
            company_id="demo-company",
            nombre="Sucursal Centro",
            geo="19.4326,-99.1332",
            serie_cfdi="A",
            suc_hash="hash-demo-sucursal"
        )
        db.add(suc)
        usr = Usuario(
            company_id="demo-company",
            rol="cajero",
            device_id="device-001",
            usr_hash="hash-demo-usuario"
        )
        db.add(usr)
        evt = EventoFacturacion(
            company_id="demo-company",
            csf_id=csf.id,
            sucursal_id=suc.id,
            usuario_id=usr.id,
            subtotal=100.0,
            impuestos=16.0,
            total=116.0,
            metodo_pago="PUE",
            forma_pago="03",
            timestamp=datetime.utcnow(),
            evt_hash="hash-demo-evento"
        )
        db.add(evt)
        db.commit()
        print("Seeds inserted successfully!")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
