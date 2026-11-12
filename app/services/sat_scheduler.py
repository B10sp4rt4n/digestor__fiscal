"""Bounded SAT reads, with durable leases and explicit per-document enrollment."""
import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import inspect, or_, update
from app.core.auth import SecurityContext
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.sat_tracking_job import SatTrackingJob

logger = logging.getLogger(__name__)
LEASE_SECONDS = 300  # More than the bounded SAT HTTP timeout; survives process restarts.


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def allowed(tenant):
    return (settings.SAT_TRACKING_AUTOPOLL and tenant in settings.SAT_TRACKING_TENANTS
            and not settings.FISCAL_MAINTENANCE_MODE
            and settings.AUTH_CONNECTOR.strip().lower() == 'jwt' and not settings.AUTH_ALLOW_ANONYMOUS
            and settings.timbracfdi_environment == 'production'
            and bool(settings.FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC))


def available(db):
    return inspect(db.connection()).has_table(SatTrackingJob.__tablename__)


def view(db, draft, folio, ctx):
    schema_ready = available(db)
    configured = allowed(draft.company_id) and schema_ready
    row = (db.query(SatTrackingJob).filter_by(company_id=draft.company_id, document_uuid=folio).first()
           if schema_ready else None)
    return {'available': configured, 'can_manage': ctx.role in ('operator', 'admin', 'superadmin'),
            'enabled': bool(row and row.enabled), 'running': bool(configured and row and row.enabled),
            'interval_seconds': settings.SAT_TRACKING_INTERVAL_SECONDS,
            'next_check_at': row.next_check_at.isoformat()+'Z' if configured and row and row.enabled else None,
            'failures': row.failures if row else 0, 'last_outcome': row.last_outcome if row else None}


def configure(db, draft, payment_id, folio, content_hash, ctx, enabled):
    from app.api.routers.payments_v1 import get_draft
    from app.services import audit_service
    if ctx.role not in ('operator', 'admin', 'superadmin'):
        raise HTTPException(403, 'Se requiere permiso de operación para cambiar el seguimiento')
    if not available(db) or (enabled and not allowed(draft.company_id)):
        raise HTTPException(403, 'Seguimiento periódico no habilitado para este tenant')
    # Serialize enrollment for the same draft. Never contacts SAT here.
    get_draft(db, draft.id, ctx, lock=True)
    db.refresh(draft)
    from app.services.sat_tracking import document
    if document(db, draft, payment_id, folio)[2] != content_hash:
        raise HTTPException(409, 'Cambió el XML durante la inscripción')
    row = db.query(SatTrackingJob).filter_by(company_id=draft.company_id, document_uuid=folio).first()
    if row and (row.draft_id != draft.id or row.payment_id != payment_id or row.document_hash != content_hash):
        raise HTTPException(409, 'El UUID ya tiene otro vínculo o cambió su evidencia')
    if row and row.enabled == enabled:
        return view(db, draft, folio, ctx)
    if not row:
        if not enabled: return view(db, draft, folio, ctx)
        row = SatTrackingJob(id=str(uuid4()), company_id=draft.company_id, draft_id=draft.id,
            payment_id=payment_id, document_uuid=folio, document_hash=content_hash, failures=0)
        db.add(row)
    row.enabled, row.enabled_by = enabled, ctx.user_id
    row.next_check_at, row.updated_at = now(), now()
    row.lease_token = row.lease_until = None
    row.failures, row.last_outcome = 0, None
    db.flush()
    audit_service.create_audit_log(db, draft.company_id, ctx.user_id, 'sat_schedule_changed',
        'sat_document', draft.id+':'+folio,
        details={'enabled': enabled, 'document_uuid': folio, 'document_hash': content_hash, 'payment_id': payment_id})
    return view(db, draft, folio, ctx)


def claim(db, at):
    # Disabled tenants are filtered before locking, so one tenant cannot starve others.
    if not settings.SAT_TRACKING_AUTOPOLL or not settings.SAT_TRACKING_TENANTS: return None
    query = db.query(SatTrackingJob).filter(SatTrackingJob.enabled.is_(True),
        SatTrackingJob.company_id.in_(settings.SAT_TRACKING_TENANTS), SatTrackingJob.next_check_at <= at,
        or_(SatTrackingJob.lease_until.is_(None), SatTrackingJob.lease_until <= at))
    row = query.order_by(SatTrackingJob.next_check_at, SatTrackingJob.id).with_for_update(skip_locked=True).first()
    if not row: return None
    token = str(uuid4())
    # Compare-and-set also protects test/dev SQLite, which has no FOR UPDATE.
    changed = db.execute(update(SatTrackingJob).where(SatTrackingJob.id == row.id,
        SatTrackingJob.enabled.is_(True),
        or_(SatTrackingJob.lease_until.is_(None), SatTrackingJob.lease_until <= at))
        .values(lease_token=token, lease_until=at+timedelta(seconds=LEASE_SECONDS)),
        execution_options={'synchronize_session': False}).rowcount
    job_id = row.id
    db.commit()  # No transaction/row lock across SAT I/O.
    return (job_id, token) if changed else None


def process(db, job_id, token):
    from app.api.routers.sat_tracking_v1 import context
    from app.services import sat_tracking
    row = db.query(SatTrackingJob).filter_by(id=job_id, lease_token=token, enabled=True).first()
    if not row or not allowed(row.company_id) or not row.lease_until or row.lease_until <= now(): return
    # Capture immutable claim identity before consult commits/expires the ORM state.
    tenant, draft_id, payment_id, folio, content_hash = row.company_id, row.draft_id, row.payment_id, row.document_uuid, row.document_hash
    failures = row.failures
    ctx = SecurityContext(tenant, 'system:sat-tracking', 'operator')
    blocked = False
    try:
        draft, root, folio, actual_hash = context(db, draft_id, payment_id, folio, ctx)
        if actual_hash != content_hash: raise HTTPException(409, 'Cambió el XML inscrito')
        result = sat_tracking.consult(db, draft, payment_id, root, folio, content_hash, ctx, trigger='scheduled')
        outcome = result['latest_attempt']['outcome']
        failures = failures+1 if outcome == 'query_failed' else 0
    except HTTPException:
        db.rollback()
        outcome, blocked = 'document_changed', True
    except Exception:
        db.rollback()
        # Do not expose exception details, raw XML, tokens or provider payloads.
        outcome, failures = 'worker_failed', failures+1
        logger.warning('SAT scheduled read failed; retained evidence is unchanged')
    # Exponential retry: 15m, 30m, 1h, ... capped at the configured normal interval.
    delay = min(settings.SAT_TRACKING_INTERVAL_SECONDS, 900 * (2 ** min(max(failures-1, 0), 8))) if failures else settings.SAT_TRACKING_INTERVAL_SECONDS
    at = now()
    db.execute(update(SatTrackingJob).where(SatTrackingJob.id == job_id,
        SatTrackingJob.lease_token == token, SatTrackingJob.enabled.is_(True)).values(
            enabled=not blocked, last_outcome=outcome, failures=failures, updated_at=at,
            next_check_at=at+timedelta(seconds=delay), lease_token=None, lease_until=None),
        execution_options={'synchronize_session': False})
    db.commit()


def run_batch(session_factory=SessionLocal, stop=None):
    # One request at a time, bounded work per tick. Each process uses the same durable leases.
    if not any(allowed(tenant) for tenant in settings.SAT_TRACKING_TENANTS): return 0
    count = 0
    for _ in range(settings.SAT_TRACKING_BATCH_SIZE):
        if stop and stop.is_set(): break
        with session_factory() as db:
            leased = claim(db, now())
        if not leased: break
        with session_factory() as db:
            process(db, *leased)
        count += 1
    return count


class SatTrackingWorker:
    def __init__(self):
        self.task = None
        self.stop_requested = None
        self.thread_stop = None

    async def start(self):
        if not any(allowed(tenant) for tenant in settings.SAT_TRACKING_TENANTS): return
        with SessionLocal() as db:
            if not available(db): raise RuntimeError('Apply reviewed SAT tracking migration before enabling periodic reads')
        if self.task: return
        self.stop_requested = asyncio.Event()
        self.thread_stop = threading.Event()
        self.task = asyncio.create_task(self.loop())

    async def loop(self):
        while not self.thread_stop.is_set():
            try:
                await asyncio.to_thread(run_batch, SessionLocal, self.thread_stop)
            except Exception:
                logger.warning('SAT tracking batch failed; will retry on the next tick')
            try:
                await asyncio.wait_for(self.stop_requested.wait(), settings.SAT_TRACKING_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        # Wait for the current bounded HTTP read; do not start another document.
        if self.task:
            self.thread_stop.set()
            self.stop_requested.set()
            await self.task
            self.task = None


sat_tracking_worker = SatTrackingWorker()
