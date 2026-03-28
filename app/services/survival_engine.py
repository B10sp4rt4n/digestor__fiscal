"""
Survival Engine — cola offline con firma HMAC y sync idempotente.
"""
import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.evento import EventoFacturacion


def _sign_payload(payload: str) -> str:
    key = settings.API_KEY.encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def verify_signature(payload: str, signature: str) -> bool:
    expected = _sign_payload(payload)
    return hmac.compare_digest(expected, signature)


def sign_batch(events: List[Dict[str, Any]]) -> str:
    payload = json.dumps(events, sort_keys=True, default=str)
    return _sign_payload(payload)


def sync_batch(db: Session, events: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Inserta eventos de forma idempotente.
    Si evt_hash ya existe, skippea sin error.
    """
    inserted = 0
    skipped = 0
    errors = 0

    for raw in events:
        try:
            evt_hash = raw.get("evt_hash")
            if not evt_hash:
                errors += 1
                continue

            # Idempotencia: si ya existe, skip
            exists = db.query(EventoFacturacion).filter_by(evt_hash=evt_hash).first()
            if exists:
                skipped += 1
                continue

            ts_raw = raw.get("timestamp")
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw)
            elif isinstance(ts_raw, (int, float)):
                ts = datetime.utcfromtimestamp(ts_raw)
            else:
                ts = datetime.utcnow()

            evt = EventoFacturacion(
                company_id=raw.get("company_id", settings.TENANT_ID),
                csf_id=raw.get("csf_id"),
                sucursal_id=raw.get("sucursal_id"),
                usuario_id=raw.get("usuario_id"),
                subtotal=float(raw.get("subtotal", 0)),
                impuestos=float(raw.get("impuestos", 0)),
                total=float(raw.get("total", 0)),
                metodo_pago=raw.get("metodo_pago"),
                forma_pago=raw.get("forma_pago"),
                timestamp=ts,
                evt_hash=evt_hash,
            )
            db.add(evt)
            db.commit()
            inserted += 1
        except IntegrityError:
            db.rollback()
            skipped += 1
        except Exception:
            db.rollback()
            errors += 1

    return {"inserted": inserted, "skipped": skipped, "errors": errors}


def get_changes(
    db: Session,
    since: Optional[str],
    limit: int = 200,
    company_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Devuelve eventos modificados desde `since` (ISO datetime)."""
    query = db.query(EventoFacturacion)
    if company_id:
        query = query.filter(EventoFacturacion.company_id == company_id)
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            query = query.filter(EventoFacturacion.timestamp >= since_dt)
        except ValueError:
            pass
    rows = query.order_by(EventoFacturacion.timestamp.asc()).limit(limit).all()
    result = []
    for r in rows:
        result.append({
            "id": r.id,
            "company_id": r.company_id,
            "csf_id": r.csf_id,
            "sucursal_id": r.sucursal_id,
            "usuario_id": r.usuario_id,
            "subtotal": r.subtotal,
            "impuestos": r.impuestos,
            "total": r.total,
            "metodo_pago": r.metodo_pago,
            "forma_pago": r.forma_pago,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "evt_hash": r.evt_hash,
        })
    return result
