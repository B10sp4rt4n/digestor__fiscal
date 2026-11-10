from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.auth import SecurityContext
from app.core.config import settings
from app.db.session import get_db
from app.api.routers.payments_v1 import get_draft, read_roles
from app.services import sat_tracking as service

router=APIRouter(prefix='/v1/billing/drafts',tags=['sat-tracking-v1'])


def context(db,draft_id,payment_id,expected_uuid,ctx):
    if settings.AUTH_CONNECTOR.strip().lower() != 'jwt' or settings.AUTH_ALLOW_ANONYMOUS:
        raise HTTPException(403,'El seguimiento SAT productivo requiere identidad autenticada')
    draft=get_draft(db,draft_id,ctx)
    root,folio,content_hash=service.document(db,draft,payment_id,str(expected_uuid))
    if settings.timbracfdi_environment != 'production':
        raise HTTPException(403,'La consulta directa SAT requiere un documento del entorno productivo')
    if draft.emitter_rfc != settings.FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC:
        raise HTTPException(403,'Emisor fuera del alcance de seguimiento SAT')
    return draft,root,folio,content_hash


def read(draft_id,payment_id,expected_uuid,ctx,db):
    draft,root,folio,content_hash=context(db,draft_id,payment_id,expected_uuid,ctx)
    return service.read(db,draft,payment_id,folio,content_hash,ctx)


def consult(draft_id,payment_id,expected_uuid,ctx,db):
    draft,root,folio,content_hash=context(db,draft_id,payment_id,expected_uuid,ctx)
    return service.consult(db,draft,payment_id,root,folio,content_hash,ctx)


@router.get('/{draft_id}/sat-status')
def invoice_read(draft_id:str,expected_uuid:UUID=Query(...),ctx:SecurityContext=Depends(read_roles),db:Session=Depends(get_db)):
    return read(draft_id,None,expected_uuid,ctx,db)


@router.post('/{draft_id}/sat-status')
def invoice_consult(draft_id:str,expected_uuid:UUID=Query(...),ctx:SecurityContext=Depends(read_roles),db:Session=Depends(get_db)):
    return consult(draft_id,None,expected_uuid,ctx,db)


@router.get('/{draft_id}/payments/{payment_id}/receipt/sat-status')
def receipt_read(draft_id:str,payment_id:str,expected_uuid:UUID=Query(...),ctx:SecurityContext=Depends(read_roles),db:Session=Depends(get_db)):
    return read(draft_id,payment_id,expected_uuid,ctx,db)


@router.post('/{draft_id}/payments/{payment_id}/receipt/sat-status')
def receipt_consult(draft_id:str,payment_id:str,expected_uuid:UUID=Query(...),ctx:SecurityContext=Depends(read_roles),db:Session=Depends(get_db)):
    return consult(draft_id,payment_id,expected_uuid,ctx,db)
