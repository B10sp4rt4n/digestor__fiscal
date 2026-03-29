from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard
from app.db.session import get_db
from app.models.csf import CSF
from app.schemas.sync_outbound import OutboundSyncRequest, OutboundSyncResponse, OutboundSyncStatusResponse
from app.services import audit_service, sync_outbound_service

router = APIRouter(prefix="/v1/sync", tags=["sync-v1"])


@router.post("/outbound", response_model=OutboundSyncResponse)
def enqueue_outbound_sync(
    body: OutboundSyncRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, body.company_id)

    if body.contract_version != "v1.0":
        raise HTTPException(status_code=400, detail="contract_version no soportada.")

    csf = db.query(CSF).filter(CSF.id == body.document.document_id).first()
    if not csf or csf.company_id != cid:
        raise HTTPException(status_code=404, detail="Documento no encontrado para el tenant.")

    if csf.processing_status != "approved_for_sync":
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "NOT_APPROVED_FOR_SYNC",
                "message": "El documento aun no fue aprobado para sincronizacion.",
                "details": {"document_id": body.document.document_id},
            },
        )

    event, created = sync_outbound_service.enqueue_outbound_event(db, cid, idempotency_key, body)

    audit_service.create_audit_log(
        db,
        company_id=cid,
        user_id=ctx.user_id,
        action="sync_outbound_enqueued",
        entity_type="document_sync_event",
        entity_id=event.id,
        details={
            "event_id": event.event_id,
            "idempotency_key": idempotency_key,
            "created": created,
            "delivery_status": event.delivery_status,
        },
        status="success",
    )

    return OutboundSyncResponse(
        accepted=True,
        event_id=event.event_id,
        delivery_status=event.delivery_status,
    )


@router.get("/outbound/{event_id}", response_model=OutboundSyncStatusResponse)
def get_outbound_status(
    event_id: str,
    company_id: str | None = None,
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, company_id)
    event = sync_outbound_service.get_by_event_id(db, cid, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Evento outbound no encontrado.")

    return OutboundSyncStatusResponse(
        event_id=event.event_id,
        company_id=event.company_id,
        document_id=event.document_id,
        delivery_status=event.delivery_status,
        attempts=event.attempts,
        last_error=event.last_error,
        last_attempt_at=event.last_attempt_at,
        next_retry_at=event.next_retry_at,
        delivered_at=event.delivered_at,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )
