"""
Seed de datos demo — crea una empresa, sucursal, usuario y evento de facturación.
Uso: python seeds/seed_demo.py
"""
from app.db.session import SessionLocal, engine
from app.db.base import Base

# Registrar modelos
import app.models.csf       # noqa: F401
import app.models.sucursal  # noqa: F401
import app.models.usuario   # noqa: F401
import app.models.evento    # noqa: F401

from app.models.sucursal import Sucursal
from app.models.usuario import Usuario
from app.models.evento import EventoFacturacion
from datetime import datetime
import uuid


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        suc = Sucursal(
            id=str(uuid.uuid4()),
            company_id="demo-company",
            nombre="Sucursal Centro",
            geo="19.4326,-99.1332",
            serie_cfdi="A",
            suc_hash="hash-demo-sucursal",
        )
        db.merge(suc)

        usr = Usuario(
            id=str(uuid.uuid4()),
            company_id="demo-company",
            rol="cajero",
            device_id="device-001",
            usr_hash="hash-demo-usuario",
        )
        db.merge(usr)

        evt = EventoFacturacion(
            id=str(uuid.uuid4()),
            company_id="demo-company",
            csf_id=None,
            sucursal_id=suc.id,
            usuario_id=usr.id,
            subtotal=100.0,
            impuestos=16.0,
            total=116.0,
            metodo_pago="PUE",
            forma_pago="03",
            timestamp=datetime.utcnow(),
            evt_hash="hash-demo-evento",
        )
        db.merge(evt)
        db.commit()
        print("✅ Seeds insertados correctamente.")
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
