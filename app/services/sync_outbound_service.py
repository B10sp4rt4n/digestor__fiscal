import json

from sqlalchemy.orm import Session

from app.models.document_sync_event import DocumentSyncEvent
from app.schemas.sync_outbound import OutboundSyncRequest


def get_by_idempotency_key(db: Session, company_id: str, idempotency_key: str) -> DocumentSyncEvent | None:
    return (
        db.query(DocumentSyncEvent)
        .filter_by(company_id=company_id, idempotency_key=idempotency_key)
        .first()
    )


def get_by_event_id(db: Session, company_id: str, event_id: str) -> DocumentSyncEvent | None:
    return (
        db.query(DocumentSyncEvent)
        .filter_by(company_id=company_id, event_id=event_id)
        .first()
    )


def enqueue_outbound_event(
    db: Session,
    company_id: str,
    idempotency_key: str,
    body: OutboundSyncRequest,
) -> tuple[DocumentSyncEvent, bool]:
    existing = get_by_idempotency_key(db, company_id, idempotency_key)
    if existing:
        return existing, False

    event = DocumentSyncEvent(
        company_id=company_id,
        idempotency_key=idempotency_key,
        contract_version=body.contract_version,
        event_type=body.event_type,
        event_id=body.event_id,
        document_id=body.document.document_id,
        delivery_status="queued",
        attempts=0,
        next_retry_at=None,
        payload_json=json.dumps(body.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event, True
