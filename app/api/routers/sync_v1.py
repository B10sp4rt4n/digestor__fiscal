import secrets
import json

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard
from app.db.session import get_db
from app.models.csf import CSF
from app.schemas.sync_outbound import OutboundDocument, OutboundSyncRequest, OutboundSyncResponse, OutboundSyncStatusResponse
from app.services import audit_service, sync_outbound_service, approval_service

router = APIRouter(prefix="/v1/sync", tags=["sync-v1"])


@router.post(
    "/outbound",
    response_model=OutboundSyncResponse,
    summary="4) Enviar documento aprobado al flujo outbound",
    description="Puedes mandar solo `document.document_id`. Se usa el snapshot aprobado. `Idempotency-Key` es opcional, pero debes enviarla para reintentos seguros.",
)
def enqueue_outbound_sync(
    body: OutboundSyncRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", description="Opcional. Si lo omites, se genera automáticamente.", examples=["demo-key-001"]),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, body.company_id)

    if body.contract_version != "v1.0":
        raise HTTPException(status_code=400, detail="contract_version no soportada.")

    if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 255):
        raise HTTPException(422, detail="Idempotency-Key debe tener entre 1 y 255 caracteres no vacíos.")
    idempotency_key = idempotency_key or f"auto-{secrets.token_hex(16)}"

    # A retry retrieves the original operation, even after reingestion/reapproval.
    existing = sync_outbound_service.get_by_idempotency_key(db, cid, idempotency_key)
    if existing:
        sync_outbound_service.check_replay(existing, body)
        return OutboundSyncResponse(
            accepted=True, event_id=existing.event_id, delivery_status=existing.delivery_status,
            delivery_mode=existing.delivery_mode, idempotency_key=existing.idempotency_key,
        )

    csf = db.query(CSF).filter_by(id=body.document.document_id, company_id=cid).with_for_update().first()
    if not csf:
        raise HTTPException(404, detail="Documento no encontrado para el tenant.")
    snapshot = approval_service.approved_document(csf)
    # Caller may echo the snapshot, never substitute approved data or quality.
    for name, value in body.document.model_dump(mode="json").items():
        if value is not None and value != snapshot.get(name):
            raise HTTPException(409, detail={
                "error_code": "APPROVED_PAYLOAD_MISMATCH",
                "message": "El contenido enviado no coincide con el documento aprobado.",
                "details": {"field": name}, "retryable": False,
            })
    body = body.model_copy(deep=True)
    body.company_id = cid
    body.document = OutboundDocument.model_validate(snapshot)
    try:
        event, created = sync_outbound_service.enqueue_outbound_event(db, cid, idempotency_key, body, commit=False)
        if created:
            audit_service.create_audit_log(
                db, company_id=cid, user_id=ctx.user_id, action="sync_outbound_enqueued",
                entity_type="document_sync_event", entity_id=event.id,
                details={"event_id": event.event_id, "idempotency_key": idempotency_key,
                         "approval_id": csf.approval_id, "delivery_status": event.delivery_status},
                status="success", commit=False,
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return OutboundSyncResponse(
        accepted=True, event_id=event.event_id, delivery_status=event.delivery_status,
        delivery_mode=event.delivery_mode, idempotency_key=event.idempotency_key,
    )


@router.get(
    "/outbound/{event_id}",
    response_model=OutboundSyncStatusResponse,
    summary="5) Consultar estado y evidencia del outbound",
    description="Devuelve el estado y un `payload_preview` del contenido persistido. `simulated` identifica NOOP; `delivered` con delivery_mode=http indica respuesta HTTP exitosa, no procesamiento empresarial. Registros legacy sin modo no prueban recepción.",
)
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

    payload_preview = None
    try:
        payload = json.loads(event.payload_json)
        doc = payload.get("document") or {}
        payload_preview = {
            "event_type": payload.get("event_type"),
            "event_time": payload.get("event_time"),
            "document": {
                "document_id": doc.get("document_id"),
                "approval_id": doc.get("approval_id"),
                "csf_hash": doc.get("csf_hash"),
                "document_type": doc.get("document_type"),
                "normalized_payload": doc.get("normalized_payload"),
                "quality": doc.get("quality"),
            },
        }
    except Exception:
        payload_preview = None

    return OutboundSyncStatusResponse(
        event_id=event.event_id,
        company_id=event.company_id,
        document_id=event.document_id,
        delivery_status=event.delivery_status,
        delivery_mode=event.delivery_mode,
        payload_preview=payload_preview,
        attempts=event.attempts,
        last_error=event.last_error,
        last_attempt_at=event.last_attempt_at,
        next_retry_at=event.next_retry_at,
        delivered_at=event.delivered_at,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )
