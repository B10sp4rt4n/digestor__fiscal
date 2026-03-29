import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta

import requests
from sqlalchemy import and_, or_

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.document_sync_event import DocumentSyncEvent
from app.services import audit_service

logger = logging.getLogger(__name__)


def _retry_delay_seconds(attempt_number: int) -> int:
    schedule = [60, 300, 900, 3600, 21600, 86400]
    idx = min(max(attempt_number - 1, 0), len(schedule) - 1)
    return schedule[idx]


def _sign_payload(payload: str) -> str:
    return hmac.new(settings.API_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _deliver_event(db, event: DocumentSyncEvent) -> None:
    payload = event.payload_json
    now = datetime.utcnow()
    event.attempts = (event.attempts or 0) + 1
    event.last_attempt_at = now

    if not settings.SYNC_OUTBOUND_WEBHOOK_URL:
        if settings.SYNC_OUTBOUND_ALLOW_NOOP_TARGET:
            event.delivery_status = "delivered"
            event.delivered_at = now
            event.last_error = None
            db.commit()
            return

        error_msg = "SYNC_OUTBOUND_WEBHOOK_URL no configurado"
        if event.attempts >= settings.SYNC_OUTBOUND_MAX_ATTEMPTS:
            event.delivery_status = "failed_dlq"
            event.last_error = error_msg
            event.next_retry_at = None
        else:
            event.delivery_status = "retry"
            event.last_error = error_msg
            event.next_retry_at = now + timedelta(seconds=_retry_delay_seconds(event.attempts))
        db.commit()
        return

    headers = {
        "Content-Type": "application/json",
        "X-Signature": _sign_payload(payload),
        "X-Event-Id": event.event_id,
    }

    try:
        response = requests.post(
            settings.SYNC_OUTBOUND_WEBHOOK_URL,
            data=payload,
            headers=headers,
            timeout=15,
        )
        if response.status_code < 300:
            event.delivery_status = "delivered"
            event.delivered_at = now
            event.last_error = None
            event.next_retry_at = None
        else:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    except Exception as exc:
        error_msg = str(exc)
        if event.attempts >= settings.SYNC_OUTBOUND_MAX_ATTEMPTS:
            event.delivery_status = "failed_dlq"
            event.last_error = error_msg
            event.next_retry_at = None
        else:
            event.delivery_status = "retry"
            event.last_error = error_msg
            event.next_retry_at = now + timedelta(seconds=_retry_delay_seconds(event.attempts))
    db.commit()


def process_outbound_batch(limit: int = 25) -> int:
    db = SessionLocal()
    processed = 0
    now = datetime.utcnow()
    try:
        events = (
            db.query(DocumentSyncEvent)
            .filter(
                DocumentSyncEvent.delivery_status.in_(["queued", "retry"]),
                or_(DocumentSyncEvent.next_retry_at.is_(None), DocumentSyncEvent.next_retry_at <= now),
            )
            .order_by(DocumentSyncEvent.created_at.asc())
            .limit(limit)
            .all()
        )

        for event in events:
            prev_status = event.delivery_status
            _deliver_event(db, event)
            processed += 1

            audit_service.create_audit_log(
                db,
                company_id=event.company_id,
                user_id="system-outbound-worker",
                action="sync_outbound_delivery_attempt",
                entity_type="document_sync_event",
                entity_id=event.id,
                details={
                    "event_id": event.event_id,
                    "previous_status": prev_status,
                    "new_status": event.delivery_status,
                    "attempts": event.attempts,
                    "next_retry_at": event.next_retry_at.isoformat() if event.next_retry_at else None,
                },
                status="success" if event.delivery_status == "delivered" else "error",
                error_message=event.last_error,
            )

        return processed
    finally:
        db.close()


class OutboundDeliveryWorker:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                processed = await asyncio.to_thread(process_outbound_batch)
                if processed > 0:
                    logger.info("Outbound worker processed %s events", processed)
            except Exception:
                logger.exception("Outbound worker loop failure")
            await asyncio.sleep(settings.SYNC_OUTBOUND_POLL_SECONDS)


outbound_delivery_worker = OutboundDeliveryWorker()
