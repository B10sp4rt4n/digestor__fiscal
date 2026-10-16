"""Synthetic characterization tests, isolated SQLite; no production app lifespan."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.models import csf, evento, sucursal, usuario, user, document_job, document_sync_event, audit_log
from app.api.routers import documents_v1, sync_v1, auth_router
from app.services import document_queue, outbound_delivery_service, audit_service
from app.services.jwt_service import create_access_token


@compiles(JSONB, 'sqlite')
def sqlite_jsonb(type_, compiler, **kw):
    return 'JSON'


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    for name, value in {
        'AUTH_CONNECTOR': 'jwt', 'AUTH_ALLOW_ANONYMOUS': False,
        'GROQ_PARSER_ENABLED': False, 'AI_FIELD_CORRECTION_ENABLED': False,
        'AI_FIELD_VALIDATION_ENABLED': False, 'GEO_CP_ENABLED': False,
        'GEO_ADDRESS_ENABLED': False, 'GEO_OPENCAGE_ENABLED': False,
        'SAT_ONLINE_VALIDATION': False, 'UPLOAD_DIR': str(tmp_path),
        'SYNC_OUTBOUND_WEBHOOK_URL': None, 'SYNC_OUTBOUND_ALLOW_NOOP_TARGET': True,
    }.items():
        monkeypatch.setattr(settings, name, value)
    def no_network(*args, **kwargs):
        raise AssertionError('Network disabled during audit')
    monkeypatch.setattr('requests.sessions.Session.request', no_network)
    app = FastAPI()
    for router in (documents_v1.router, sync_v1.router, auth_router.router):
        app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(document_queue, 'SessionLocal', factory)
    tasks = []
    async def capture(task):
        tasks.append(task)
    monkeypatch.setattr(document_queue.document_queue, 'enqueue', capture)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=db, tasks=tasks)
    db.close()
    engine.dispose()


def headers(tenant='tenant-a', role='operator', key=None):
    result = {'Authorization': 'Bearer ' + create_access_token({'sub': 'synthetic-user', 'tenant': tenant, 'role': role})}
    if key is not None:
        result['Idempotency-Key'] = key
    return result


def seed(env, **overrides):
    values = dict(company_id='tenant-a', rfc='XAXX010101000', razon_social='EMPRESA SINTETICA', regimen='General', cp='01234', id_cif='123456789', csf_hash='synthetic-hash', processing_status='approved_for_sync')
    values.update(overrides)
    row = csf.CSF(**values)
    env.db.add(row)
    env.db.commit()
    return row


def outbound(env, row, key=None, **extra):
    return env.client.post('/v1/sync/outbound', headers=headers(key=key), json={'document': {'document_id': row.id}, **extra})


def test_synthetic_pdf_upload_worker_approve_outbound(env, monkeypatch):
    import fitz
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((50, 50), 'CONSTANCIA DE SITUACION FISCAL\nRFC: XAXX010101000\nDenominación/Razón Social: EMPRESA SINTETICA\nRégimen: General\nCódigo Postal: 01234\nidCIF: 123456789')
    response = env.client.post('/v1/documents', headers=headers(), files={'file': ('synthetic.pdf', pdf.tobytes(), 'application/pdf')})
    assert response.status_code == 200
    job_id = response.json()['job_id']
    assert response.json()['status'] == 'queued'
    document_queue._process_document_task(env.tasks.pop())
    status = env.client.get('/v1/documents/' + job_id, headers=headers()).json()
    assert status['status'] == 'done', status
    doc_id = status['document_id']
    assert env.client.post(f'/v1/documents/{doc_id}/approve', headers=headers(), json={}).status_code == 200
    row = env.db.get(csf.CSF, doc_id)
    response = outbound(env, row, 'synthetic-flow')
    assert response.status_code == 200  # Current contract differs from target 202.
    event_id = response.json()['event_id']
    event = env.db.query(document_sync_event.DocumentSyncEvent).filter_by(event_id=event_id).one()
    monkeypatch.setattr(settings, 'SYNC_OUTBOUND_WEBHOOK_URL', 'https://receiver.invalid/synthetic')
    sent = []
    def receiver(*args, **kwargs):
        sent.append(kwargs)
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(outbound_delivery_service.requests, 'post', receiver)
    outbound_delivery_service._deliver_event(env.db, event)
    assert json.loads(sent[0]['data'])['document']['document_id'] == doc_id
    assert 'X-Signature' in sent[0]['headers']
    result = env.client.get('/v1/sync/outbound/' + event_id, headers=headers())
    assert result.json()['delivery_status'] == 'delivered'
    assert env.client.get('/v1/documents/' + job_id, headers=headers('tenant-b')).status_code == 403
    assert env.client.get('/v1/sync/outbound/' + event_id, headers=headers('tenant-b')).status_code == 404


def test_approval_currently_accepts_invalid_fields_without_done_job(env):
    row = seed(env, rfc='INVALID', cp='bad', processing_status='needs_review', field_validation=[{'field': 'tax_id', 'status': 'no'}])
    response = env.client.post(f'/v1/documents/{row.id}/approve', headers=headers(), json={})
    assert response.status_code == 200  # Characterization, NOT desired quality policy.


def test_missing_key_creates_two_events(env):
    row = seed(env)
    assert outbound(env, row).json()['event_id'] != outbound(env, row).json()['event_id']


def test_same_key_returns_old_event_even_for_changed_document(env):
    first = seed(env)
    second = seed(env, csf_hash='other-synthetic-hash')
    assert outbound(env, first, 'stable').json()['event_id'] == outbound(env, second, 'stable').json()['event_id']


def test_noop_delivered_is_not_network_delivery(env):
    row = seed(env)
    outbound(env, row, 'noop')
    event = env.db.query(document_sync_event.DocumentSyncEvent).one()
    outbound_delivery_service._deliver_event(env.db, event)
    assert event.delivery_status == 'delivered'
    assert event.attempts == 1


def test_retry_then_dlq(env, monkeypatch):
    row = seed(env)
    outbound(env, row, 'retry')
    event = env.db.query(document_sync_event.DocumentSyncEvent).one()
    monkeypatch.setattr(settings, 'SYNC_OUTBOUND_ALLOW_NOOP_TARGET', False)
    monkeypatch.setattr(settings, 'SYNC_OUTBOUND_MAX_ATTEMPTS', 2)
    outbound_delivery_service._deliver_event(env.db, event)
    assert event.delivery_status == 'retry' and event.next_retry_at
    outbound_delivery_service._deliver_event(env.db, event)
    assert event.delivery_status == 'failed_dlq' and event.next_retry_at is None


def test_tenant_and_role_guards(env):
    row = seed(env)
    path = f'/v1/documents/{row.id}/approve'
    assert env.client.post(path, headers=headers('tenant-b'), json={}).status_code == 403
    assert env.client.post(path, headers=headers(role='viewer'), json={}).status_code == 403
    assert env.client.post(path, json={}).status_code == 401
    assert env.client.post('/v1/sync/outbound', headers=headers('tenant-b'), json={'document': {'document_id': row.id}}).status_code == 404


def test_audit_actor_tampering_is_currently_not_detected(env):
    log = audit_service.create_audit_log(env.db, 'tenant-a', 'original', 'test', 'csf', 'synthetic')
    log.user_id = 'changed'
    env.db.commit()
    assert audit_service.verify_audit_chain(env.db, 'tenant-a')[0] is True


def test_tenant_admin_cannot_create_superadmin(env):
    result = env.client.post('/auth/register', headers=headers(role='admin'), json={'username': 'synthetic-new-user', 'password': 'synthetic-password', 'tenant_id': 'tenant-a', 'role': 'superadmin'})
    assert result.status_code == 403


@pytest.mark.parametrize('actor,assigned', [('admin', 'operator'), ('admin', 'admin'), ('superadmin', 'superadmin')])
def test_registration_preserves_authorized_roles(env, actor, assigned):
    result = env.client.post('/auth/register', headers=headers(role=actor), json={'username': 'synthetic-authorized', 'password': 'synthetic-password', 'tenant_id': 'tenant-a', 'role': assigned})
    assert result.status_code == 201
    assert result.json()['role'] == assigned
