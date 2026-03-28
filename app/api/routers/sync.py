"""
Router de sincronización offline — /sync/batch y /sync/changes
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard
from app.db.session import get_db
from app.schemas.evento import SyncBatchRequest, SyncBatchResult, SyncChangesResult
from app.services import survival_engine

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/batch", response_model=SyncBatchResult)
def sync_batch(
    payload: SyncBatchRequest,
    company_id: Optional[str] = Query(default=None, description="Tenant objetivo (superadmin)"),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    # Verificar firma HMAC si viene incluida
    if payload.signature:
        raw = json.dumps(payload.events, sort_keys=True, default=str)
        if not survival_engine.verify_signature(raw, payload.signature):
            raise HTTPException(status_code=403, detail="Firma HMAC inválida.")

    tenant_id = enforce_tenant_scope(ctx, company_id)
    normalized_events = []
    for event in payload.events:
        event_copy = dict(event)
        if ctx.role == "superadmin":
            event_copy["company_id"] = event_copy.get("company_id") or tenant_id
        else:
            event_copy["company_id"] = tenant_id
        normalized_events.append(event_copy)

    result = survival_engine.sync_batch(db, normalized_events)
    return SyncBatchResult(**result)


@router.get("/changes", response_model=SyncChangesResult)
def get_changes(
    company_id: Optional[str] = Query(default=None, description="Tenant objetivo (superadmin)"),
    since: Optional[str] = Query(default=None, description="ISO datetime desde donde traer cambios"),
    limit: int = Query(default=200, le=1000),
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    tenant_id = enforce_tenant_scope(ctx, company_id)
    events = survival_engine.get_changes(db, since=since, limit=limit, company_id=tenant_id)
    return SyncChangesResult(events=events, total=len(events))
