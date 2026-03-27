"""
Router de sincronización offline — /sync/batch y /sync/changes
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.evento import SyncBatchRequest, SyncBatchResult, SyncChangesResult
from app.services import survival_engine

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/batch", response_model=SyncBatchResult)
def sync_batch(
    payload: SyncBatchRequest,
    db: Session = Depends(get_db),
):
    # Verificar firma HMAC si viene incluida
    if payload.signature:
        raw = json.dumps(payload.events, sort_keys=True, default=str)
        if not survival_engine.verify_signature(raw, payload.signature):
            raise HTTPException(status_code=403, detail="Firma HMAC inválida.")

    result = survival_engine.sync_batch(db, payload.events)
    return SyncBatchResult(**result)


@router.get("/changes", response_model=SyncChangesResult)
def get_changes(
    since: Optional[str] = Query(default=None, description="ISO datetime desde donde traer cambios"),
    limit: int = Query(default=200, le=1000),
    db: Session = Depends(get_db),
):
    events = survival_engine.get_changes(db, since=since, limit=limit)
    return SyncChangesResult(events=events, total=len(events))
