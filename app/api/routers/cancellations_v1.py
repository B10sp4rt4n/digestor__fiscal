"""Explicit, scoped cancellation; PAC acknowledgement never substitutes SAT status."""
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.auth import SecurityContext
from app.core.config import settings
from app.core.fiscal_sandbox import fiscal_operation_allowed, fiscal_operation_is_production
from app.db.session import get_db
from app.api.routers.payments_v1 import get_draft, read_roles, write_roles
from app.schemas.billing_cancellation import CancelDocument
from app.services import billing_cancellations as service, timbracfdi_client as pac
from app.services.fiscal_acts import digest
from app.services import fiscal_production as production
from app.models.fiscal_revision import FiscalRevision

router = APIRouter(prefix='/v1/billing/drafts', tags=['cancellations-v1'])


def enabled():
    if not settings.FISCAL_CANCELLATION_ENABLED or not fiscal_operation_allowed('cancellation'):
        raise HTTPException(status_code=503, detail='Transporte de cancelación no autorizado')


def present(value):
    return {**value, 'environment':settings.timbracfdi_environment}


def read(draft_id, ctx, db, payment_id=None):
    enabled(); draft = get_draft(db, draft_id, ctx)
    production.scope('cancellation',draft,ctx)
    _, folio, _ = service.target(db,draft,payment_id)
    old = production.legacy(db,draft,folio,ctx) if not payment_id else None
    return present(old or service.view(service.find(db, draft, payment_id)))


def request(draft_id, body, ctx, db, payment_id=None, source=None):
    enabled(); live_mode = fiscal_operation_is_production('cancellation')
    draft = get_draft(db,draft_id,ctx,lock=not live_mode)
    production.scope('cancellation',draft,ctx,mutate=True)
    checked_source = source or service.target(db,draft,payment_id)
    old = production.legacy(db,draft,checked_source[1],ctx) if not payment_id else None
    if old: return present(old)
    existing = service.find(db,draft,payment_id,document_uuid=checked_source[1])
    if live_mode and (not existing or existing.status == 'not_sent'):
        replacement_root = replacement_folio = None
        if body.replacement_revision_id:
            revision = db.query(FiscalRevision).filter_by(id=str(body.replacement_revision_id),company_id=draft.company_id,
                draft_id=draft.id,payment_id=payment_id,source_uuid=checked_source[1]).first()
            if not revision or revision.status != 'stamped' or not revision.result_xml:
                service.fail('Confirma primero la sustitución exacta')
            service.revisions.verify(db,revision)
            replacement_root = service.revisions.parse(revision.result_xml); replacement_folio = revision.result_uuid
        elif body.replacement_draft_id:
            replacement = get_draft(db,str(body.replacement_draft_id),ctx)
            replacement_root,replacement_folio,_ = service.target(db,replacement)
        db.commit()  # Release reads before SAT. The document is rechecked under lock below.
        production.sat(checked_source[0],checked_source[1],cancelable=True)
        if replacement_root is not None: production.sat(replacement_root,replacement_folio)
    draft = get_draft(db,draft_id,ctx,lock=True)
    production.scope('cancellation',draft,ctx,mutate=True)
    if not source and service.target(db,draft,payment_id)[1:] != checked_source[1:]:
        service.fail('Cambió el comprobante durante la consulta SAT')
    try:
        row, claimed = service.reserve(db, draft, body, ctx, payment_id, source=source)
        if not claimed: return present(service.view(row))
        emitter, intent = draft.emitter_rfc, json.loads(row.intent)
        row_id, folio = row.id, row.document_uuid
        db.commit()  # Durable reservation before PAC, no lock across network.
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail='Solicitud ya registrada; consulta su estado') from exc
    result, not_called = None, False
    try:
        if live_mode:
            code, raw = production.live.cancel_at_pac(emitter,folio,intent['reason'],intent['replacement_uuid'] or '')
            ack = production.live.acknowledgement(raw,folio,emitter) if code == 200 else None
            result = {'ok':code == 200,'status_code':code,'provider_response':{'XmlAcuse':ack,'raw':raw}}
        else:
            result = pac.cancel_cfdi(emitter, folio, intent['reason'], intent['replacement_uuid'])
    except pac.TimbraCFDIConfigurationError: not_called = True
    except Exception: pass  # Ambiguous outcomes never allow automatic resubmission.
    draft = get_draft(db, draft_id, ctx, lock=True)
    row = service.find(db, draft, payment_id, lock=True, document_uuid=folio)
    if not row or row.id != row_id: service.fail('Cambió la solicitud de cancelación')
    if row.status != 'processing': return present(service.view(row))
    row.provider_response = json.dumps(result, ensure_ascii=False) if result is not None else None
    ack = service.acknowledgement(result or {}, row, emitter)
    row.acknowledgement_xml = ack
    row.status = 'not_sent' if not_called else ('requested' if ack else 'unknown')
    service.append_act(db, row, ctx, 'provider_response', 'processing', {'response_sha256': digest(row.provider_response or ''),
        'ack_sha256': digest(ack) if ack else None})
    db.commit()
    return present(service.view(row))


def reconcile(draft_id, ctx, db, payment_id=None, source=None):
    enabled(); draft = get_draft(db, draft_id, ctx)
    production.scope('cancellation',draft,ctx,mutate=True)
    root, folio, document_hash = source or service.target(db, draft, payment_id)
    old = production.legacy(db,draft,folio,ctx) if not payment_id else None
    if old:
        return present(old if old['status']=='cancelled' else production.live.reconcile(db,draft.id,ctx))
    row = service.find(db, draft, payment_id, document_uuid=folio)
    if not row: service.fail('Primero debe existir una solicitud de cancelación')
    if row.document_hash != document_hash: service.fail('Cambió el XML del comprobante')
    if row.status == 'cancelled': return present(service.view(row))
    db.commit()
    try:
        if fiscal_operation_is_production('cancellation'):
            result = production.live.sat_status(root,folio)
            next_status = production.cancellation_status(result)
        else:
            result = pac.query_cfdi_status(folio); next_status = service.sat_status(result,folio)
    except Exception as exc:
        raise HTTPException(status_code=502, detail='Consulta sin confirmar; no se repitió la cancelación') from exc
    draft = get_draft(db, draft_id, ctx, lock=True)
    row = service.find(db, draft, payment_id, lock=True, document_uuid=folio)
    if row.status == 'cancelled': return present(service.view(row))
    before = row.status; row.status = next_status or before; row.checked_at = datetime.utcnow()
    service.append_act(db, row, ctx, 'status_consulted', before, {'response_sha256': digest(json.dumps(result, sort_keys=True)), 'status_response':result})
    db.commit()
    return present(service.view(row))


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
