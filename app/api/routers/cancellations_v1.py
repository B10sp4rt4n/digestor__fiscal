"""Explicit cancellation and read-only provider reconciliation in sandbox."""
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.auth import SecurityContext
from app.core.config import settings
from app.core.fiscal_sandbox import rest_transport_is_sandbox
from app.db.session import get_db
from app.api.routers.payments_v1 import get_draft, read_roles, write_roles
from app.schemas.billing_cancellation import CancelDocument
from app.services import billing_cancellations as service, timbracfdi_client as pac
from app.services.fiscal_acts import digest

router = APIRouter(prefix='/v1/billing/drafts', tags=['cancellations-v1'])


def enabled():
    if not settings.FISCAL_CANCELLATION_ENABLED or not rest_transport_is_sandbox():
        raise HTTPException(status_code=503, detail='Cancelaciones limitadas a pruebas y deshabilitadas por defecto')


def read(draft_id, ctx, db, payment_id=None):
    enabled(); draft = get_draft(db, draft_id, ctx)
    service.target(db, draft, payment_id)
    return service.view(service.find(db, draft, payment_id))


def request(draft_id, body, ctx, db, payment_id=None, source=None):
    enabled(); draft = get_draft(db, draft_id, ctx, lock=True)
    try:
        row, claimed = service.reserve(db, draft, body, ctx, payment_id, source=source)
        if not claimed: return service.view(row)
        emitter, intent = draft.emitter_rfc, json.loads(row.intent)
        row_id, folio = row.id, row.document_uuid
        db.commit()  # Intent and reservation durable before the external operation.
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail='Solicitud ya registrada; consulta su estado') from exc
    result, not_called = None, False
    try:
        result = pac.cancel_cfdi(emitter, folio, intent['reason'], intent['replacement_uuid'])
    except pac.TimbraCFDIConfigurationError:
        not_called = True
    except Exception:
        pass  # Unknown outcome retains reservation; never resubmit automatically.
    draft = get_draft(db, draft_id, ctx, lock=True)
    row = service.find(db, draft, payment_id, lock=True, document_uuid=folio)
    if not row or row.id != row_id: service.fail('Cambió la solicitud de cancelación')
    # A concurrent status query can already have confirmed cancellation. Never regress it.
    if row.status != 'processing': return service.view(row)
    row.provider_response = json.dumps(result, ensure_ascii=False) if result is not None else None
    ack = service.acknowledgement(result or {}, row, emitter)
    row.acknowledgement_xml = ack
    row.status = 'not_sent' if not_called else ('requested' if ack else 'unknown')
    service.append_act(db, row, ctx, 'provider_response', 'processing', {'response_sha256': digest(row.provider_response or ''),
        'ack_sha256': digest(ack) if ack else None})
    db.commit()
    return service.view(row)


def reconcile(draft_id, ctx, db, payment_id=None, source=None):
    enabled(); draft = get_draft(db, draft_id, ctx)
    _, folio, document_hash = source or service.target(db, draft, payment_id)
    row = service.find(db, draft, payment_id, document_uuid=folio)
    if not row: service.fail('Primero debe existir una solicitud de cancelación')
    if row.document_hash != document_hash: service.fail('Cambió el XML del comprobante')
    if row.status == 'cancelled': return service.view(row)
    db.commit()  # No row lock across a network consultation.
    try: result = pac.query_cfdi_status(folio)
    except Exception as exc:
        raise HTTPException(status_code=502, detail='Consulta sin confirmar; no se repitió la cancelación') from exc
    draft = get_draft(db, draft_id, ctx, lock=True)
    row = service.find(db, draft, payment_id, lock=True, document_uuid=folio)
    if row.status == 'cancelled': return service.view(row)
    before = row.status
    row.status = service.sat_status(result, folio) or before
    row.checked_at = datetime.utcnow()
    service.append_act(db, row, ctx, 'status_consulted', before, {'response_sha256': digest(json.dumps(result, sort_keys=True))})
    db.commit()
    return service.view(row)


@router.get('/{draft_id}/cancellation')
def read_invoice(draft_id: str, ctx: SecurityContext=Depends(read_roles), db: Session=Depends(get_db)):
    return read(draft_id, ctx, db)


@router.post('/{draft_id}/cancellation')
def cancel_invoice(draft_id: str, body: CancelDocument, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    return request(draft_id, body, ctx, db)


@router.post('/{draft_id}/cancellation/reconcile')
def reconcile_invoice(draft_id: str, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    return reconcile(draft_id, ctx, db)


@router.get('/{draft_id}/payments/{payment_id}/receipt/cancellation')
def read_receipt(draft_id: str, payment_id: str, ctx: SecurityContext=Depends(read_roles), db: Session=Depends(get_db)):
    return read(draft_id, ctx, db, payment_id)


@router.post('/{draft_id}/payments/{payment_id}/receipt/cancellation')
def cancel_receipt(draft_id: str, payment_id: str, body: CancelDocument, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    return request(draft_id, body, ctx, db, payment_id)


@router.post('/{draft_id}/payments/{payment_id}/receipt/cancellation/reconcile')
def reconcile_receipt(draft_id: str, payment_id: str, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    return reconcile(draft_id, ctx, db, payment_id)
