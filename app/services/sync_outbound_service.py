import json

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.document_sync_event import DocumentSyncEvent
from app.schemas.sync_outbound import OutboundSyncRequest


def get_by_idempotency_key(db, company_id, idempotency_key):
    return db.query(DocumentSyncEvent).filter_by(company_id=company_id, idempotency_key=idempotency_key).first()


def get_by_event_id(db, company_id, event_id):
    return db.query(DocumentSyncEvent).filter_by(company_id=company_id, event_id=event_id).first()


def conflict(message='La clave ya identifica otra operación.'):
    raise HTTPException(409, detail={
        'error_code': 'IDEMPOTENCY_CONFLICT', 'message': message,
        'details': {}, 'retryable': False,
    })


def check_replay(event, body):
    """Compare intent; omitted generated metadata refers to the original operation.

    Omitted document fields refer to the stored snapshot, not today's mutable row.
    Explicit metadata and document fields must match. Legacy rows remain replayable.
    """
    try:
        saved = json.loads(event.payload_json)
        document = saved['document']
    except (ValueError, KeyError, TypeError):
        conflict('El evento previo no tiene evidencia legible; requiere revisión.')
    incoming = body.model_dump(mode='json')
    for name in ('contract_version', 'event_type'):
        if incoming[name] != saved.get(name):
            conflict()
    for name in ('event_id', 'event_time'):
        if name in body.model_fields_set and incoming[name] != saved.get(name):
            conflict()
    for name, value in incoming['document'].items():
        if value is not None and value != document.get(name):
            conflict()
    return event, False


def enqueue_outbound_event(db: Session, company_id: str, idempotency_key: str,
                           body: OutboundSyncRequest, commit: bool = True):
    existing = get_by_idempotency_key(db, company_id, idempotency_key)
    if existing:
        return check_replay(existing, body)
    event = DocumentSyncEvent(
        company_id=company_id, idempotency_key=idempotency_key,
        contract_version=body.contract_version, event_type=body.event_type,
        event_id=body.event_id, document_id=body.document.document_id,
        delivery_status='queued', attempts=0, next_retry_at=None,
        payload_json=json.dumps(body.model_dump(mode='json'), ensure_ascii=False, sort_keys=True),
    )
    try:
        # This operation owns its transaction; the router has made no other writes.
        db.add(event)
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = get_by_idempotency_key(db, company_id, idempotency_key)
        if existing:
            return check_replay(existing, body)
        conflict('event_id ya está en uso; usa otro identificador para una operación nueva.')
    if commit:
        db.commit()
        db.refresh(event)
    return event, True
