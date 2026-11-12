"""Actual PostgreSQL outbox and bounded synthetic transport, with no HTTP or PAC."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from app.services.sat_metadata_import import digest, encoded
from app.services.sat_metadata_outbox import DisposableMetadataOutbox
from tests.test_sat_metadata_postgres import pg_store
from tests.test_sat_metadata_import import AUTH, ingest, line, txt, scope


@pytest.fixture
def queued(pg_store):
    target = DisposableMetadataOutbox(pg_store.dsn, schema='sat_out_test_'+uuid4().hex)
    result = ingest(pg_store, txt(line()))
    delivery = target.enqueue_package(pg_store, result['package_key'], scope(), **AUTH)
    try:
        yield target, delivery, pg_store, result
    finally:
        target.reverse()


def acknowledgment(body):
    return {'status': 'accepted', 'receipt_id': '44444444-4444-4444-8444-444444444444',
        'batch_id': body['batch_id'], 'dataset': body['dataset'], 'schema_version': body['schema_version'],
        'tenant_id': 'tenant-a', 'empresa_id': 'company-a', 'empresa_rfc': 'AAA010101AAA',
        'payload_sha256': digest(encoded(body).encode()), 'received_records': len(body['records']),
        'new_records': 1, 'coverage': 'partial'}


def ready(target):
    with target.connection() as db:
        db.execute("UPDATE deliveries SET next_attempt=clock_timestamp()-interval '1 second'")


def test_enqueue_reopen_and_ack_persisted(queued):
    target, delivery, source, result = queued
    assert target.enqueue_package(source, result['package_key'], scope(), **AUTH) == delivery
    reopened = DisposableMetadataOutbox(target.dsn, schema=target.schema)
    completed = reopened.dispatch(delivery, scope(), acknowledgment, **AUTH)
    assert completed['status'] == 'acknowledged'
    assert reopened.dispatch(delivery, scope(), lambda body: pytest.fail('must not resend'), **AUTH)['ack'] == completed['ack']


def test_lost_response_resends_exact_payload_and_recovers_same_receipt(queued):
    target, delivery, _, _ = queued
    received = []
    def transport(body):
        received.append(encoded(body))
        if len(received) == 1: raise TimeoutError('synthetic response lost after receiver commit')
        return acknowledgment(body)
    assert target.dispatch(delivery, scope(), transport, **AUTH)['status'] == 'retry'
    assert target.claim(delivery, scope(), **AUTH) is None  # backoff enforced
    ready(target)
    assert target.dispatch(delivery, scope(), transport, **AUTH)['status'] == 'acknowledged'
    assert len(received) == 2 and received[0] == received[1]


@pytest.mark.parametrize('field,value', [('tenant_id','foreign'), ('payload_sha256','0'*64),
                                       ('batch_id',str(uuid4())), ('received_records',2), ('new_records',True)])
def test_invalid_ack_blocks_without_confirming(queued, field, value):
    target, delivery, _, _ = queued
    def wrong(body):
        return acknowledgment(body) | {field: value}
    row = target.dispatch(delivery, scope(), wrong, **AUTH)
    assert row['status'] == 'blocked' and row['ack'] is None and row['last_error'] == 'invalid_ack'


def test_two_connections_only_one_claim(queued):
    target, delivery, _, _ = queued
    other = DisposableMetadataOutbox(target.dsn, schema=target.schema)
    barrier = Barrier(2)
    def claim(obj):
        barrier.wait(timeout=5)
        return obj.claim(delivery, scope(), **AUTH)
    with ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(claim, [target, other]))
    assert sum(row is not None for row in rows) == 1


def test_expired_claim_late_ack_cannot_overwrite_new_token(queued):
    target, delivery, _, _ = queued
    def late(body):
        with target.connection() as db:
            db.execute("UPDATE deliveries SET lease_until=clock_timestamp()-interval '1 second'")
        newer = target.claim(delivery, scope(), **AUTH)
        assert newer is not None
        return acknowledgment(body)
    result = target.dispatch(delivery, scope(), late, **AUTH)
    assert result['status'] == 'inflight' and result['ack'] is None and result['attempts'] == 2


def test_attempt_limit_and_cross_tenant_guard(queued):
    target, delivery, _, _ = queued
    with pytest.raises(ValueError): target.read(delivery, scope(), **(AUTH | {'authenticated_tenant':'foreign'}))
    def failed(body): raise TimeoutError('not logged')
    for _ in range(5):
        ready(target)
        result = target.dispatch(delivery, scope(), failed, **AUTH)
    assert result['status'] == 'blocked' and result['attempts'] == 5
    assert target.claim(delivery, scope(), **AUTH) is None
