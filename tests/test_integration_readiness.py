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


def seed(env, approved=True, **overrides):
    values = dict(company_id='tenant-a', rfc='XAXX010101000', razon_social='EMPRESA SINTETICA', regimen='General', cp='01234', id_cif='123456789', csf_hash='synthetic-hash', processing_status='processed')
    values.update(overrides)
    row = csf.CSF(**values)
    env.db.add(row)
    env.db.commit()
    from app.services import approval_service, ingest
    if row.field_validation is None:
        row.field_validation = ingest._build_field_validation_report({key: getattr(row, attr) for key, attr in approval_service.FIELD_MAP.items()})
    env.db.add(document_job.DocumentJob(company_id=row.company_id, document_id=row.id, document_type='csf', status='done'))
    env.db.commit()
    if approved:
        approval_service.approve(env.db, row, 'synthetic-user', None)
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


def test_approval_rejects_invalid_fields(env):
    row = seed(env, approved=False, rfc='INVALID', cp='bad', processing_status='needs_review', field_validation=[{'field': 'tax_id', 'status': 'no'}])
    response = env.client.post(f'/v1/documents/{row.id}/approve', headers=headers(), json={})
    assert response.status_code == 422


def test_missing_key_creates_two_events(env):
    row = seed(env)
    assert outbound(env, row).json()['event_id'] != outbound(env, row).json()['event_id']


def test_same_key_rejects_changed_document(env):
    first = seed(env)
    second = seed(env, csf_hash='other-synthetic-hash')
    assert outbound(env, first, 'stable').status_code == 200
    assert outbound(env, second, 'stable').status_code == 409


def test_noop_is_explicitly_simulated(env):
    row = seed(env)
    outbound(env, row, 'noop')
    event = env.db.query(document_sync_event.DocumentSyncEvent).one()
    outbound_delivery_service._deliver_event(env.db, event)
    assert event.delivery_status == 'simulated'
    assert event.delivery_mode == 'noop'
    assert event.delivered_at is None
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


@pytest.mark.parametrize('field,value', [('rfc', 'INVALID'), ('cp', 'abc'), ('id_cif', 'abc'), ('razon_social', 'SIN NOMBRE')])
def test_approval_validates_formats_and_placeholders(env, field, value):
    row = seed(env, approved=False, **{field: value})
    response = env.client.post(f'/v1/documents/{row.id}/approve', headers=headers(), json={})
    assert response.status_code == 422
    assert row.approval_id is None


@pytest.mark.parametrize('state,error', [('processing', None), ('failed', 'parse failure'), ('done', 'parse failure')])
def test_approval_requires_successful_job(env, state, error):
    row = seed(env, approved=False)
    job = env.db.query(document_job.DocumentJob).filter_by(document_id=row.id).one()
    job.status, job.error = state, error
    env.db.commit()
    assert env.client.post(f'/v1/documents/{row.id}/approve', headers=headers(), json={}).status_code == 422


def test_legacy_document_requires_v1_reingestion(env):
    row = seed(env, approved=False)
    env.db.query(document_job.DocumentJob).delete()
    env.db.commit()
    assert env.client.post(f'/v1/documents/{row.id}/approve', headers=headers(), json={}).status_code == 422


@pytest.mark.parametrize('report', [None, [], [{'field': 'tax_id', 'status': 'no', 'is_ok': False}]])
def test_approval_requires_complete_current_report(env, report):
    row = seed(env, approved=False)
    row.field_validation = report
    env.db.commit()
    assert env.client.post(f'/v1/documents/{row.id}/approve', headers=headers(), json={}).status_code == 422


def test_approval_is_stable_on_retry_and_records_snapshot(env):
    row = seed(env)
    first = (row.approval_id, row.approved_at, row.approved_by)
    count = env.db.query(audit_log.AuditLog).count()
    result = env.client.post(f'/v1/documents/{row.id}/approve', headers=headers(), json={})
    assert result.status_code == 200
    assert (row.approval_id, row.approved_at, row.approved_by) == first
    assert row.approval_snapshot['normalized_payload']['rfc'] == row.rfc
    assert env.db.query(audit_log.AuditLog).count() == count


def test_approval_rolls_back_when_audit_fails(env, monkeypatch):
    row = seed(env, approved=False)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(audit_service, 'create_audit_log', fail)
    with pytest.raises(RuntimeError, match='synthetic audit failure'):
        env.client.post(f'/v1/documents/{row.id}/approve', headers=headers(), json={})
    env.db.expire_all()
    assert row.approval_id is None
    assert row.processing_status == 'processed'


def test_replay_survives_changed_document_and_new_key_requires_reapproval(env):
    row = seed(env)
    first = outbound(env, row, 'stable').json()
    row.cp = '99999'
    env.db.commit()
    assert outbound(env, row, 'stable').json()['event_id'] == first['event_id']
    assert outbound(env, row, 'new-key').json()['detail']['error_code'] == 'APPROVAL_STALE'


@pytest.mark.parametrize('field,value', [('csf_hash', 'forged'), ('normalized_payload', {'rfc': 'forged'}), ('quality', {'required_fields_ok': False}), ('approval_id', 'forged'), ('document_type', 'other')])
def test_caller_cannot_replace_approved_content(env, field, value):
    row = seed(env)
    response = env.client.post('/v1/sync/outbound', headers=headers(key='tamper'), json={'document': {'document_id': row.id, field: value}})
    assert response.status_code == 409
    assert env.db.query(document_sync_event.DocumentSyncEvent).count() == 0


def test_replay_checks_explicit_fields_and_event_metadata(env):
    row = seed(env)
    first = outbound(env, row, 'stable', event_id='synthetic-id')
    assert first.status_code == 200
    assert outbound(env, row, 'stable').json()['event_id'] == 'synthetic-id'
    assert outbound(env, row, 'stable', event_id='different-id').status_code == 409
    assert outbound(env, row, 'stable', event_type='different-type').status_code == 409
    assert outbound(env, row, 'different-key', event_id='synthetic-id').status_code == 409
    bad = env.client.post('/v1/sync/outbound', headers=headers(key='stable'), json={'document': {'document_id': row.id, 'csf_hash': 'forged'}})
    assert bad.status_code == 409


def test_outbound_persists_tenant_and_server_snapshot(env):
    row = seed(env)
    result = outbound(env, row).json()
    assert result['idempotency_key'].startswith('auto-')
    event = env.db.query(document_sync_event.DocumentSyncEvent).one()
    payload = json.loads(event.payload_json)
    assert payload['company_id'] == 'tenant-a'
    assert payload['document'] == row.approval_snapshot
    assert outbound(env, row, result['idempotency_key']).json()['event_id'] == result['event_id']


def test_legacy_approval_cannot_enqueue_new_event(env):
    row = seed(env, approved=False, processing_status='approved_for_sync')
    assert outbound(env, row, 'legacy').status_code == 422


def test_key_scope_is_per_tenant(env):
    first = seed(env)
    second = seed(env, company_id='tenant-b', csf_hash='tenant-b-hash')
    result_a = outbound(env, first, 'shared').json()
    result_b = env.client.post('/v1/sync/outbound', headers=headers('tenant-b', key='shared'), json={'document': {'document_id': second.id}})
    assert result_b.status_code == 200
    assert result_a['event_id'] != result_b.json()['event_id']


def test_delivery_timeout_and_redirect_never_report_success(env, monkeypatch):
    import requests
    row = seed(env)
    outbound(env, row, 'network-failure')
    event = env.db.query(document_sync_event.DocumentSyncEvent).one()
    monkeypatch.setattr(settings, 'SYNC_OUTBOUND_WEBHOOK_URL', 'https://receiver.invalid')
    def timeout(*args, **kwargs):
        assert kwargs['allow_redirects'] is False
        raise requests.Timeout('synthetic timeout')
    monkeypatch.setattr(outbound_delivery_service.requests, 'post', timeout)
    outbound_delivery_service._deliver_event(env.db, event)
    assert event.delivery_status == 'retry' and event.delivered_at is None
    monkeypatch.setattr(outbound_delivery_service.requests, 'post', lambda *a, **k: SimpleNamespace(status_code=302, text='redirect'))
    outbound_delivery_service._deliver_event(env.db, event)
    assert event.delivery_status == 'retry' and event.delivery_mode == 'http'


def test_real_loopback_receiver_deduplicates_repeat_delivery(env, monkeypatch):
    """Actual HTTP on loopback only; demonstrates receiver-owned deduplication."""
    import hashlib
    import hmac
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import Request, urlopen
    received, effects = [], set()
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            data = self.rfile.read(int(self.headers['Content-Length']))
            assert hmac.compare_digest(self.headers['X-Signature'], hmac.new(settings.API_KEY.encode(), data, hashlib.sha256).hexdigest())
            payload = json.loads(data)
            received.append(payload)
            effects.add((payload['company_id'], self.headers['X-Event-Id']))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"accepted":true}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}/receive'
        monkeypatch.setattr(settings, 'SYNC_OUTBOUND_WEBHOOK_URL', url)
        # Keep the global requests blocker; bridge ONLY this exact loopback URL.
        def local_post(target, data, headers, **kwargs):
            assert target == url
            with urlopen(Request(target, data=data.encode(), headers=headers, method='POST'), timeout=2) as response:
                return SimpleNamespace(status_code=response.status, text=response.read().decode())
        monkeypatch.setattr(outbound_delivery_service.requests, 'post', local_post)
        row = seed(env)
        outbound(env, row, 'loopback')
        event = env.db.query(document_sync_event.DocumentSyncEvent).one()
        outbound_delivery_service._deliver_event(env.db, event)
        outbound_delivery_service._deliver_event(env.db, event)  # Simulate redelivery after lost ACK.
        assert event.delivery_status == 'delivered' and event.delivery_mode == 'http'
        assert len(received) == 2 and len(effects) == 1
        assert received[0]['document'] == row.approval_snapshot
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_enqueue_rolls_back_when_audit_fails(env, monkeypatch):
    row = seed(env)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(audit_service, 'create_audit_log', fail)
    with pytest.raises(RuntimeError, match='synthetic audit failure'):
        outbound(env, row, 'atomic')
    assert env.db.query(document_sync_event.DocumentSyncEvent).count() == 0


def test_new_migration_roundtrip_on_isolated_schema():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/20261016_01_approval_snapshot_delivery_mode.py'
    spec = importlib.util.spec_from_file_location('approval_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE csf (id VARCHAR PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE document_sync_events (id VARCHAR PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            assert 'approval_snapshot' in {c['name'] for c in inspect(connection).get_columns('csf')}
            assert 'delivery_mode' in {c['name'] for c in inspect(connection).get_columns('document_sync_events')}
            module.downgrade()
            assert {c['name'] for c in inspect(connection).get_columns('csf')} == {'id'}
    engine.dispose()


def test_worker_blocks_legacy_payload_without_approval_evidence(env):
    event = document_sync_event.DocumentSyncEvent(company_id='tenant-a', document_id='legacy', idempotency_key='legacy-key', contract_version='v1.0', event_type='csf_sync', event_id='legacy-event', payload_json='{"document":{"document_id":"legacy"}}')
    env.db.add(event)
    env.db.commit()
    outbound_delivery_service._deliver_event(env.db, event)
    assert event.delivery_status == 'blocked_review'
    assert event.delivered_at is None


def test_reapproval_creates_new_snapshot_but_old_key_stays_old_operation(env):
    from app.services import ingest, approval_service
    row = seed(env)
    original = outbound(env, row, 'original').json()
    old_id = row.approval_id
    row.cp = '99999'
    row.field_validation = ingest._build_field_validation_report({key: getattr(row, attr) for key, attr in approval_service.FIELD_MAP.items()})
    env.db.commit()
    result = env.client.post(f'/v1/documents/{row.id}/approve', headers=headers(), json={})
    assert result.status_code == 200 and row.approval_id != old_id
    assert outbound(env, row, 'original').json()['event_id'] == original['event_id']
    new = outbound(env, row, 'new-revision')
    assert new.status_code == 200 and new.json()['event_id'] != original['event_id']


def test_concurrent_insert_recovery_uses_winning_event(env, monkeypatch):
    """Inject uniqueness race deterministically; not a PostgreSQL concurrency test."""
    from sqlalchemy.exc import IntegrityError
    from app.services import sync_outbound_service
    from app.schemas.sync_outbound import OutboundSyncRequest
    row = seed(env)
    result = outbound(env, row, 'race').json()
    body = OutboundSyncRequest(document={'document_id': row.id})
    original_lookup = sync_outbound_service.get_by_idempotency_key
    calls = []
    def lookup(*args):
        calls.append(1)
        return None if len(calls) == 1 else original_lookup(*args)
    def collide(*args, **kwargs):
        raise IntegrityError('synthetic insert', {}, Exception('unique race'))
    monkeypatch.setattr(sync_outbound_service, 'get_by_idempotency_key', lookup)
    monkeypatch.setattr(env.db, 'flush', collide)
    # After rollback restore normal flush for the winning read.
    rollback = env.db.rollback
    flush = type(env.db).flush
    def recover():
        rollback()
        monkeypatch.setattr(env.db, 'flush', lambda *a, **kw: flush(env.db, *a, **kw))
    monkeypatch.setattr(env.db, 'rollback', recover)
    recovered, created = sync_outbound_service.enqueue_outbound_event(env.db, 'tenant-a', 'race', body)
    assert not created and recovered.event_id == result['event_id']
