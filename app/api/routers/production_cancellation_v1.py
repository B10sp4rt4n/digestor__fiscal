from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from typing import Literal
from sqlalchemy.orm import Session
from app.core.auth import SecurityContext, role_guard
from app.db.session import get_db
from app.services import production_cancellation as service

router = APIRouter(prefix='/v1/billing/drafts', tags=['production-cancellation'])
authorized = role_guard('operator','admin','superadmin')

class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: Literal[True]
    expected_uuid: UUID

@router.get('/{draft_id}/production-cancellation')
def read(draft_id: str, ctx: SecurityContext=Depends(authorized), db: Session=Depends(get_db)):
    return service.read(db,draft_id,ctx)

@router.post('/{draft_id}/production-cancellation')
def submit(draft_id: str, body: Confirmation, ctx: SecurityContext=Depends(authorized), db: Session=Depends(get_db)):
    return service.submit(db,draft_id,ctx,str(body.expected_uuid))

@router.post('/{draft_id}/production-cancellation/reconcile')
def reconcile(draft_id: str, ctx: SecurityContext=Depends(authorized), db: Session=Depends(get_db)):
    return service.reconcile(db,draft_id,ctx)
