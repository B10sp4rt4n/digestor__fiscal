"""Explicit, tenant-scoped payment declarations and sandbox REP lifecycle."""
import base64
import json
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.auth import SecurityContext, role_guard
from app.core.config import settings
from app.core.fiscal_sandbox import ppd_transport_is_sandbox
from app.db.session import get_db
from app.models.commercial import BillingDraft
from app.models.billing_payment import BillingPayment, PaymentReceipt
from app.schemas.billing_payment import RegisterPayment
from app.services import payment_receipts as service, timbracfdi_client as pac
from app.services.fiscal_acts import digest
from app.services.rep_validation import validate as validate_rep
from app.services.timbracfdi_client import TimbraCFDIConfigurationError

router = APIRouter(prefix='/v1/billing/drafts', tags=['payments-v1'])
read_roles = role_guard('viewer','operator','admin','superadmin')
write_roles = role_guard('operator','admin','superadmin')


def enabled():
    if not settings.FISCAL_PPD_ENABLED:
        raise HTTPException(status_code=503,detail='PPD y pagos aún no están habilitados')
    # This first implementation may only issue PPD/REP in sandbox.
    if not ppd_transport_is_sandbox():
        raise HTTPException(status_code=503,detail='PPD está limitado a validación sandbox')


def get_draft(db, draft_id, ctx, lock=False):
    query = db.query(BillingDraft).filter_by(id=draft_id,company_id=ctx.tenant_id)
    if lock: query = query.with_for_update()
    draft = query.first()
    if not draft: raise HTTPException(status_code=404,detail='Factura no encontrada')
    return draft


def payment_view(payment,receipt):
    return {'id':payment.id, 'sequence':payment.sequence, 'reference':payment.reference,
        'amount':f'{payment.amount:.2f}','paid_at':payment.paid_at.isoformat()+'Z','payment_form':payment.payment_form,
        'balance_before':f'{payment.balance_before:.2f}','balance_after':f'{payment.balance_after:.2f}',
        'receipt_status':receipt.status,'receipt_uuid':receipt.uuid,'xml_base64':receipt.xml_base64,
        'bank_verification':'not_performed'}


def get_payment(db,draft, payment_id,lock=False):
    payment = db.query(BillingPayment).filter_by(id=payment_id,company_id=draft.company_id,draft_id=draft.id).first()
    if not payment: raise HTTPException(status_code=404,detail='Pago no encontrado')
    query = db.query(PaymentReceipt).filter_by(payment_id=payment.id,company_id=draft.company_id)
    if lock: query = query.with_for_update()
    receipt = query.first()
    if not receipt: raise HTTPException(status_code=409,detail='Pago sin estado de complemento; requiere revisión')
    return payment,receipt


@router.get('/{draft_id}/payments')
def list_payments(draft_id: str, ctx: SecurityContext=Depends(read_roles), db: Session=Depends(get_db)):
    enabled(); draft = get_draft(db,draft_id,ctx)
    if draft.payment_method != 'PPD': return {'supported':False,'reason':'Esta factura no es PPD','payments':[]}
    snapshot = service.invoice_snapshot(draft)
    rows = db.query(BillingPayment).filter_by(company_id=draft.company_id,draft_id=draft.id).order_by(BillingPayment.sequence).all()
    balance,_,_ = service.verify_payments(rows,snapshot)
    result = []
    for payment in rows:
        _, receipt = get_payment(db,draft,payment.id)
        result.append(payment_view(payment,receipt))
    return {'supported':True, 'currency':'MXN','invoice_uuid':snapshot['uuid'],'total':snapshot['total'],
        'paid':f'{Decimal(snapshot["total"])-balance:.2f}','balance':f'{balance:.2f}',
        'receipt_stamp_enabled':settings.FISCAL_REP_STAMP_ENABLED, 'payments':result}


@router.post('/{draft_id}/payments',status_code=201)
def register_payment(draft_id: str, body: RegisterPayment, ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    enabled(); draft = get_draft(db,draft_id,ctx,lock=True)
    try:
        payment = service.register(db,draft,body,ctx)
        receipt = db.query(PaymentReceipt).filter_by(company_id=draft.company_id,payment_id=payment.id).one()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409,detail='La referencia o clave de pago ya está registrada; consulta el saldo') from exc
    return payment_view(payment,receipt)


@router.get('/{draft_id}/payments/{payment_id}/receipt')
def read_receipt(draft_id: str,payment_id: str,ctx: SecurityContext=Depends(read_roles), db: Session=Depends(get_db)):
    enabled(); draft = get_draft(db,draft_id,ctx)
    payment,receipt = get_payment(db,draft,payment_id)
    return payment_view(payment,receipt)


@router.post('/{draft_id}/payments/{payment_id}/receipt/prepare')
def prepare_receipt(draft_id: str,payment_id: str,ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    enabled(); draft = get_draft(db,draft_id,ctx,lock=True)
    snap = service.invoice_snapshot(draft)
    rows = db.query(BillingPayment).filter_by(company_id=draft.company_id,draft_id=draft.id).order_by(BillingPayment.sequence).all()
    service.verify_payments(rows,snap)
    payment,receipt = get_payment(db,draft,payment_id,lock=True)
    # Preview never changes an issued or pending request and never calls PAC.
    xml = receipt.request_xml or service.build_xml(payment)
    validation = validate_rep(xml)
    if not validation['valid']: raise HTTPException(status_code=422,detail={'message':'XML preliminar no válido para el perfil REP','validation':validation})
    return {'preliminary':True,'validation':validation,'xml_base64':base64.b64encode(xml.encode()).decode(), 'request_xml_sha256':digest(xml),
        'receipt_status':receipt.status,'bank_verification':'not_performed'}


@router.post('/{draft_id}/payments/{payment_id}/receipt/stamp')
def stamp_receipt(draft_id: str,payment_id: str,ctx: SecurityContext=Depends(write_roles), db: Session=Depends(get_db)):
    enabled()
    if not settings.FISCAL_REP_STAMP_ENABLED:
        raise HTTPException(status_code=503,detail='Timbrado REP sandbox deshabilitado')
    draft = get_draft(db,draft_id,ctx,lock=True)
    snap = service.invoice_snapshot(draft)
    rows = db.query(BillingPayment).filter_by(company_id=draft.company_id,draft_id=draft.id).order_by(BillingPayment.sequence).all()
    service.verify_payments(rows,snap)
    payment,receipt = get_payment(db,draft,payment_id,lock=True)
    if receipt.status not in {'not_sent','rejected'} or receipt.xml_base64 or receipt.uuid or receipt.stamped_at:
        raise HTTPException(status_code=409,detail='Complemento emitido o pendiente de conciliación; no repetir')
    # Keep fiscal partiality order even if several collections were declared.
    for prior in rows[:payment.sequence-1]:
        _, previous_receipt = get_payment(db,draft,prior.id)
        if previous_receipt.status != 'stamped':
            raise HTTPException(status_code=409,detail='Primero concilia o timbra el complemento de la parcialidad anterior')
    xml = service.build_xml(payment)
    validation = validate_rep(xml)
    if not validation['valid']: raise HTTPException(status_code=422,detail={'message':'XML REP no válido; no se contactó al PAC','validation':validation})
    before = receipt.status
    claimed = db.execute(update(PaymentReceipt).where(PaymentReceipt.payment_id==payment.id,
        PaymentReceipt.company_id==draft.company_id,PaymentReceipt.status.in_(('not_sent','rejected')),
        PaymentReceipt.xml_base64.is_(None),PaymentReceipt.uuid.is_(None),PaymentReceipt.stamped_at.is_(None)
        ).values(status='processing',request_xml=xml).execution_options(synchronize_session=False)).rowcount
    if claimed != 1:
        db.rollback(); raise HTTPException(status_code=409,detail='Otra solicitud reservó el complemento')
    db.refresh(receipt)
    service.append_act(db,payment,receipt,ctx,'reserved',before)
    evidence = {'schema_bundle_sha256':validation['schema_bundle_sha256'],'validation_mode':validation['mode'],'request_xml_sha256':digest(xml),'invoice_xml_sha256':snap['xml_sha256']}
    service.append_act(db,payment,receipt,ctx,'prepared','processing',evidence)
    db.commit()  # Reservation/request durable before PAC. No row lock across network.
    try:
        result = pac.timbra_cfdi(xml_base64=base64.b64encode(xml.encode()).decode(),id_comprobante='payment-'+payment.id)
    except TimbraCFDIConfigurationError as exc:
        receipt.status='not_sent'
        service.append_act(db,payment,receipt,ctx,'not_called','processing',evidence); db.commit()
        raise HTTPException(status_code=400,detail='PAC sandbox no configurado; no se envió') from exc
    except Exception as exc:
        receipt.status='unknown'
        service.append_act(db,payment,receipt,ctx,'unknown','processing',evidence); db.commit()
        raise HTTPException(status_code=502,detail='Resultado incierto del complemento; no repetir') from exc
    info = pac.extract_stamped_document(result)
    receipt.provider_response = json.dumps(result.get('provider_response'),ensure_ascii=False)
    if info['success'] and service.validate_returned_xml(xml,info['stamped_xml']):
        receipt.status='stamped'; receipt.uuid=pac.extract_stamp_uuid(info['stamped_xml'])
        receipt.xml_base64=base64.b64encode(info['stamped_xml'].encode()).decode(); receipt.stamped_at=datetime.utcnow()
        evidence['result_xml_sha256']=digest(info['stamped_xml']); action='confirmed'
    elif not info['success'] and pac.is_explicit_stamp_rejection(result):
        receipt.status='rejected'; action='rejected'
    else:
        receipt.status='unknown'; action='unknown'
    service.append_act(db,payment,receipt,ctx,action,'processing',evidence)
    db.commit()
    if receipt.status=='unknown': raise HTTPException(status_code=502,detail='PAC no confirmó el complemento esperado; requiere conciliación')
    return {'ok':receipt.status=='stamped',**payment_view(payment,receipt)}
