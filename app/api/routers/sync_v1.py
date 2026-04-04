import secrets
import json

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard
from app.db.session import get_db
from app.models.csf import CSF
from app.schemas.sync_outbound import OutboundSyncRequest, OutboundSyncResponse, OutboundSyncStatusResponse
from app.services import audit_service, sync_outbound_service

router = APIRouter(prefix="/v1/sync", tags=["sync-v1"])


@router.post(
    "/outbound",
    response_model=OutboundSyncResponse,
    summary="4) Enviar documento aprobado al flujo outbound",
    description="Puedes mandar solo `document.document_id`. El resto se auto-completa desde la BD y `Idempotency-Key` es opcional.",
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

    # Idempotency-Key es opcional; si no viene, se genera uno único
    if not idempotency_key:
        idempotency_key = f"auto-{secrets.token_hex(8)}"

    csf = db.query(CSF).filter(CSF.id == body.document.document_id).first()
    if not csf or csf.company_id != cid:
        raise HTTPException(status_code=404, detail="Documento no encontrado para el tenant.")

    # Auto-completar campos desde la BD si el caller no los envió
    if body.document.csf_hash is None:
        body.document.csf_hash = csf.csf_hash or ""
    if body.document.normalized_payload is None:
        body.document.normalized_payload = {
            "rfc": csf.rfc,
            "razon_social": csf.razon_social,
            "regimen": csf.regimen,
            "cp": csf.cp,
            "curp": csf.curp,
            "id_cif": csf.id_cif,
        }
    if body.document.quality is None:
        from app.schemas.sync_outbound import OutboundQuality
        missing = [f for f, v in {
            "rfc": csf.rfc, "razon_social": csf.razon_social,
            "regimen": csf.regimen, "cp": csf.cp, "id_cif": csf.id_cif,
        }.items() if not v]
        body.document.quality = OutboundQuality(
            required_fields_ok=len(missing) == 0,
            score=round(1.0 - len(missing) / 5, 2),
            missing_fields=missing,
        )

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


@router.get(
    "/outbound/{event_id}",
    response_model=OutboundSyncStatusResponse,
    summary="5) Consultar estado y evidencia del outbound",
    description="Devuelve el estado (`queued`, `delivered`, etc.) y un `payload_preview` con el contenido realmente enviado.",
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
        payload_preview=payload_preview,
        attempts=event.attempts,
        last_error=event.last_error,
        last_attempt_at=event.last_attempt_at,
        next_retry_at=event.next_retry_at,
        delivered_at=event.delivered_at,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )
