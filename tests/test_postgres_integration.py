"""Real PostgreSQL tests: isolated schema in a loopback-only test database.

Set DIGESTOR_POSTGRES_TEST_URL; never uses DATABASE_URL or app DB_URL.
"""
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models.document_sync_event import DocumentSyncEvent
from app.schemas.sync_outbound import OutboundSyncRequest
from app.services import sync_outbound_service, audit_service
from app.models.audit_log import AuditLog

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pg():
    raw = os.getenv('DIGESTOR_POSTGRES_TEST_URL')
    if not raw:
        pytest.skip('Requires isolated PostgreSQL test service')
    url = make_url(raw)
    if url.host not in {'127.0.0.1', 'localhost'} or url.database != 'digestor_test':
        pytest.fail('Refusing database outside loopback digestor_test')
    schema = 'test_' + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_url = url.update_query_dict({'options': f'-csearch_path={schema}', 'sslmode': 'disable'})
    engine = create_engine(test_url)
    env = dict(os.environ)
    env['DATABASE_URL'] = test_url.render_as_string(hide_password=False)
    env['DB_URL'] = env['DATABASE_URL']
    def migrate(*args):
        result = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=ROOT, env=env, capture_output=True, text=True)
        # Do not expose connection strings if a driver emits them in an exception.
        output = (result.stdout + result.stderr).replace(env['DATABASE_URL'], '[test database]')
        assert result.returncode == 0, output
    try:
        migrate('upgrade', 'head')
        yield engine, sessionmaker(bind=engine, autoflush=False), migrate
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def body(document='synthetic-document', event_id=None):
    kwargs = {} if event_id is None else {'event_id': event_id}
    return OutboundSyncRequest(company_id='synthetic-tenant', document={'document_id': document}, **kwargs)


def test_full_migration_chain_and_last_revision_roundtrip(pg):
    engine, factory, migrate = pg
    assert 'approval_snapshot' in {c['name'] for c in inspect(engine).get_columns('csf')}
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO csf (id, company_id, rfc, razon_social, csf_hash) VALUES ('synthetic-id','synthetic-tenant','XAXX010101000','SYNTHETIC','synthetic-hash')"))
    migrate('downgrade', '20260405_05')
    assert 'approval_snapshot' not in {c['name'] for c in inspect(engine).get_columns('csf')}
    migrate('upgrade', 'head')
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM csf WHERE id='synthetic-id'")).scalar_one() == 1
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20261016_01'


@pytest.mark.parametrize('same_document', [True, False])
def test_simultaneous_idempotency_insert(pg, monkeypatch, same_document):
    engine, factory, _ = pg
    barrier = threading.Barrier(2, timeout=10)
    original = sync_outbound_service.get_by_idempotency_key
    local = threading.local()
    def lookup(*args):
        result = original(*args)
        if not getattr(local, 'visited', False):
            local.visited = True
            barrier.wait()
        return result
    monkeypatch.setattr(sync_outbound_service, 'get_by_idempotency_key', lookup)
    def submit(index):
        with factory() as db:
            try:
                event, created = sync_outbound_service.enqueue_outbound_event(db, 'synthetic-tenant', 'shared-key', body('same' if same_document else f'document-{index}'))
                return 200, event.event_id, created
            except HTTPException as error:
                return error.status_code, None, False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [1, 2]))
    with factory() as db:
        assert db.query(DocumentSyncEvent).count() == 1
    if same_document:
        assert [r[0] for r in results] == [200, 200]
        assert results[0][1] == results[1][1]
        assert sum(r[2] for r in results) == 1
    else:
        assert sorted(r[0] for r in results) == [200, 409]


def test_simultaneous_global_event_id_collision(pg):
    engine, factory, _ = pg
    barrier = threading.Barrier(2, timeout=10)
    def submit(index):
        with factory() as db:
            barrier.wait()
            try:
                sync_outbound_service.enqueue_outbound_event(db, f'tenant-{index}', f'key-{index}', body(event_id='same-event'))
                return 200
            except HTTPException as error:
                return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, [1, 2])) == [200, 409]
    with factory() as db:
        assert db.query(DocumentSyncEvent).count() == 1


def test_committed_event_survives_new_session_and_replay(pg):
    _, factory, _ = pg
    with factory() as db:
        event, _ = sync_outbound_service.enqueue_outbound_event(db, 'tenant', 'recover', body())
        event_id = event.event_id
    with factory() as db:
        event, created = sync_outbound_service.enqueue_outbound_event(db, 'tenant', 'recover', body())
        assert not created and event.event_id == event_id


def test_rollback_removes_event_and_audit_together(pg):
    _, factory, _ = pg
    with factory() as db:
        event, _ = sync_outbound_service.enqueue_outbound_event(db, 'tenant', 'atomic', body(), commit=False)
        audit_service.create_audit_log(db, 'tenant', 'synthetic-user', 'enqueue', 'sync', event.id, commit=False)
        db.rollback()
    with factory() as db:
        assert db.query(DocumentSyncEvent).count() == 0
        assert db.query(AuditLog).count() == 0
