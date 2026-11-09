"""Explicit fiscal correction steps; never rewrite an issued source document."""
import base64
from uuid import uuid5, NAMESPACE_URL
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.core.auth import SecurityContext
from app.core.config import settings
from app.core.fiscal_sandbox import fiscal_operation_allowed, fiscal_operation_is_production
from app.db.session import get_db
from app.api.routers.payments_v1 import get_draft, read_roles, write_roles
from app.api.routers import cancellations_v1
from app.schemas.fiscal_revision import CreateFiscalRevision, RevisionOperation
from app.schemas.billing_cancellation import RecoverReceipt, CancelDocument
from app.services import fiscal_revisions as svc, timbracfdi_client as pac, fiscal_production as production

router = APIRouter(prefix='/v1/billing/drafts', tags=['fiscal-revisions-v1'])


def enabled():
    if not settings.FISCAL_CORRECTIONS_ENABLED or not fiscal_operation_allowed('correction'):
        raise HTTPException(status_code=503, detail='Transporte de sustitución no autorizado')
    if fiscal_operation_is_production('correction') and (not settings.FISCAL_CANCELLATION_ENABLED or not fiscal_operation_is_production('cancellation')):
        raise HTTPException(status_code=503, detail='Habilita cancelación productiva antes de sustituir')


def latest(db, draft, payment_id):
    _, row, history = svc.context(db, draft, payment_id)
    if not row: svc.fail('No existe una sustitución pendiente')
    return row


def action(draft_id, operation, body, ctx, db, payment_id=None):
    if operation != 'read': enabled()
    live_mode = fiscal_operation_is_production('correction')
    draft = get_draft(db, draft_id, ctx, lock=not live_mode)
    production.scope('correction',draft,ctx,mutate=operation != 'read')
    if live_mode and operation in {'create','stamp'}:
        xml, _, _ = svc.context(db,draft,payment_id)
        folio = pac.extract_stamp_uuid(xml).lower()
        if not payment_id and production.legacy(db,draft,folio,ctx): svc.fail('El comprobante ya tiene cancelación productiva registrada')
        db.commit()
        production.sat(svc.parse(xml),folio,cancelable=True)
        draft = get_draft(db,draft_id,ctx,lock=True)
        if svc.context(db,draft,payment_id)[0] != xml: svc.fail('Cambió el comprobante durante la consulta SAT')
    elif live_mode:
        draft = get_draft(db,draft_id,ctx,lock=True)
    if operation == 'create':
        try: svc.create(db, draft, body, ctx, payment_id); db.commit()
        except IntegrityError as exc:
            db.rollback(); raise HTTPException(status_code=409, detail='Otra solicitud registró la sustitución; consulta su estado') from exc
        return svc.view(db, draft, payment_id)
    if operation == 'read':
        value = svc.view(db,draft,payment_id)
        old = production.legacy(db,draft,value['active_uuid'],ctx) if live_mode and not payment_id else None
        cancelled = svc.cancellation(db,draft,value['active_uuid'])
        return {**value, 'can_correct':not old and cancelled is None}

    row = latest(db, draft, payment_id)
    if live_mode and (not body or str(getattr(body,'revision_id',None)) != row.id):
        svc.fail('La corrección confirmada cambió; revisa la versión actual',422)
    if operation == 'abandon':
        if row.status not in {'prepared', 'rejected'} or row.result_xml or row.result_uuid:
            svc.fail('No se puede descartar una emisión en proceso, incierta o confirmada')
        before = row.status; row.status = 'abandoned'; svc.append(db, row, ctx, 'abandoned', before); db.commit()
        return svc.view(db, draft, payment_id)
    if operation in {'cancel-original', 'confirm-cancellation'}:
        if row.status != 'stamped': svc.fail('Primero debe confirmarse el comprobante sustituto')
        cancel_body = CancelDocument(idempotency_key=uuid5(NAMESPACE_URL, 'cancel-revision:'+row.id),
            reason='01', replacement_revision_id=row.id, expected_uuid=row.source_uuid)
        source = (svc.parse(row.source_xml), row.source_uuid, svc.digest(row.source_xml))
        if operation == 'cancel-original':
            cancellations_v1.request(draft_id, cancel_body, ctx, db, payment_id, source=source)
        else:
            cancellations_v1.reconcile(draft_id, ctx, db, payment_id, source=source)
        draft = get_draft(db, draft_id, ctx, lock=True)
        return svc.view(db, draft, payment_id)
    if operation == 'stamp':
        if payment_id and not (settings.FISCAL_REP_STAMP_ENABLED and settings.FISCAL_PPD_ENABLED): svc.fail('Timbrado REP sandbox deshabilitado', 503)
        if row.status not in {'prepared', 'rejected'} or row.result_xml or row.result_uuid:
            svc.fail('La sustitución ya fue enviada o requiere conciliación; no repetir')
        before = row.status; row.status = 'processing'; svc.append(db, row, ctx, 'reserved', before)
        revision_id, request_xml, emitter = row.id, row.request_xml, draft.emitter_rfc
        db.commit()
        try:
            result = pac.timbra_cfdi(xml_base64=base64.b64encode(request_xml.encode()).decode(), id_comprobante='revision-'+revision_id)
            outcome = 'response'
        except pac.TimbraCFDIConfigurationError:
            result, outcome = None, 'not_called'
        except Exception:
            result, outcome = None, 'unknown'
    elif operation == 'reconcile':
        if row.status != 'unknown': svc.fail('Sólo se recuperan sustituciones con resultado incierto; una emisión en proceso conserva su reserva')
        revision_id, request_xml, emitter = row.id, row.request_xml, draft.emitter_rfc
        db.commit()
        try: result = pac.retrieve_cfdi(emitter, str(body.receipt_uuid))
        except Exception as exc:
            raise HTTPException(status_code=502, detail='Consulta sin confirmar; no se volvió a timbrar') from exc
        outcome = 'recovered'
    else: svc.fail('Acción no admitida', 422)
    draft = get_draft(db, draft_id, ctx, lock=True)
    row = latest(db, draft, payment_id)
    if row.id != revision_id or row.request_xml != request_xml: svc.fail('Cambió la sustitución durante la operación')
    svc.verify(db, row)
    if row.status == 'stamped': return svc.view(db, draft, payment_id)
    if row.status not in {'processing', 'unknown'}: svc.fail('El estado cambió durante la consulta')
    before = row.status
    info = pac.extract_stamped_document(result or {})
    if info['success'] and svc.validate_result(row, info['stamped_xml']):
        folio = pac.extract_stamp_uuid(info['stamped_xml']).lower()
        if operation == 'reconcile' and folio != str(body.receipt_uuid): svc.fail('El proveedor devolvió otro UUID')
        row.status = 'stamped'; row.result_uuid = folio; row.result_xml = info['stamped_xml']
    elif operation == 'reconcile':
        svc.fail('El XML recuperado no corresponde a la solicitud exacta')
    elif outcome == 'not_called': row.status = 'prepared'
    elif pac.is_explicit_stamp_rejection(result or {}): row.status = 'rejected'
    else: row.status = 'unknown'
    svc.append(db, row, ctx, outcome, before, result); db.commit()
    return svc.view(db, draft, payment_id)


@router.get('/{draft_id}/correction')
def read_invoice(draft_id: str, ctx: SecurityContext=Depends(read_roles), db: Session=Depends(get_db)):
    return action(draft_id, 'read', None, ctx, db)


@router.post('/{draft_id}/correction')
def create_invoice(draft_id: str, body: CreateFiscalRevision, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    return action(draft_id, 'create', body, ctx, db)


@router.post('/{draft_id}/correction/reconcile')
def recover_invoice(draft_id: str, body: RecoverReceipt, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    return action(draft_id, 'reconcile', body, ctx, db)


@router.post('/{draft_id}/correction/{operation}')
def invoice_action(draft_id: str, operation: Literal['stamp', 'abandon', 'cancel-original', 'confirm-cancellation'], body: RevisionOperation | None = None, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    return action(draft_id, operation, body, ctx, db)


@router.get('/{draft_id}/payments/{payment_id}/receipt/correction')
def read_receipt(draft_id: str, payment_id: str, ctx: SecurityContext=Depends(read_roles), db: Session=Depends(get_db)):
    return action(draft_id, 'read', None, ctx, db, payment_id)


@router.post('/{draft_id}/payments/{payment_id}/receipt/correction')
def create_receipt(draft_id: str, payment_id: str, body: CreateFiscalRevision, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    return action(draft_id, 'create', body, ctx, db, payment_id)


@router.post('/{draft_id}/payments/{payment_id}/receipt/correction/reconcile')
def recover_receipt(draft_id: str, payment_id: str, body: RecoverReceipt, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    return action(draft_id, 'reconcile', body, ctx, db, payment_id)


@router.post('/{draft_id}/payments/{payment_id}/receipt/correction/{operation}')
def receipt_action(draft_id: str, payment_id: str, operation: Literal['stamp', 'abandon', 'cancel-original', 'confirm-cancellation'], body: RevisionOperation | None = None, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    return action(draft_id, operation, body, ctx, db, payment_id)
