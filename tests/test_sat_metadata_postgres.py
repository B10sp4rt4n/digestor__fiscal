"""Same importer against disposable PostgreSQL, never a production DSN."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.services.sat_metadata_postgres import DisposablePostgresMetadataStore, assert_test_target
from tests.test_sat_metadata_import import AT, AUTH, ingest, latest, line, scope, txt


@pytest.fixture
def pg_store():
    dsn = os.environ.get('SAT_METADATA_TEST_DSN')
    if not dsn:
        pytest.skip('Dedicated local SAT_METADATA_TEST_DSN required')
    schema = 'sat_md_test_' + uuid4().hex
    store = DisposablePostgresMetadataStore(dsn, schema=schema)
    try:
        yield store
    finally:
        store.migrate_down()


def counts(store):
    with store._connection() as db:
        return tuple(db.execute(f'SELECT COUNT(*) AS count FROM {name}').fetchone()['count']
                     for name in ('packages', 'documents', 'observations'))


def test_migration_repeated_and_reverse_does_not_touch_other_schema(pg_store):
    ingest(pg_store, txt(line()))
    pg_store.migrate_up()
    assert counts(pg_store) == (1, 1, 1)
    other = DisposablePostgresMetadataStore(pg_store.dsn, schema='sat_md_test_'+uuid4().hex)
    try:
        ingest(other, txt(line()))
        pg_store.migrate_down()
        pg_store.migrate_down()
        assert counts(other) == (1, 1, 1)
        pg_store.migrate_up()
        assert counts(pg_store) == (0, 0, 0)
    finally:
        other.migrate_down()


def test_reopen_replay_and_overlapping_period(pg_store):
    raw = txt(line())
    first = ingest(pg_store, raw)
    other = DisposablePostgresMetadataStore(pg_store.dsn, schema=pg_store.schema)
    assert ingest(other, raw, AT.replace(hour=7)) == first | {'replayed': True}
    assert ingest(other, raw, selected=scope(period_start='2026-10-02'))['stored_observations'] == 0
    assert counts(other) == (2, 1, 1)


def test_two_connections_same_package_once(pg_store):
    other = DisposablePostgresMetadataStore(pg_store.dsn, schema=pg_store.schema)
    barrier = Barrier(2)
    def run(target):
        barrier.wait(timeout=5)
        return ingest(target, txt(line()))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, [pg_store, other]))
    assert sorted(row['replayed'] for row in results) == [False, True]
    assert counts(pg_store) == (1, 1, 1)


def test_concurrent_different_packages_keep_newest_projection(pg_store):
    other = DisposablePostgresMetadataStore(pg_store.dsn, schema=pg_store.schema)
    barrier = Barrier(2)
    def run(args):
        target, raw, at = args
        barrier.wait(timeout=5)
        return ingest(target, raw, at)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, [(pg_store, txt(line()), AT),
            (other, txt(line(Estatus='0')), AT.replace(hour=7))]))
    assert all(row['status'] == 'validated' for row in results)
    assert latest(pg_store)[0]['estatus_fiscal'] == 'cancelado'
    assert counts(pg_store) == (2, 1, 2)


def test_conflicting_uuid_rejects_whole_package_without_cash_or_partial_rows(pg_store):
    ingest(pg_store, txt(line()))
    bad = ingest(pg_store, txt(line(Monto='999'), line(Uuid=str(uuid4()))), AT.replace(hour=6))
    assert bad['status'] == 'rejected' and bad['envelope'] is None
    assert counts(pg_store) == (2, 1, 1)
    assert latest(pg_store)[0]['total'] == '116.00'


def test_failed_sql_rolls_back_every_write(pg_store):
    with pg_store._connection() as db:
        db.execute("ALTER TABLE observations ADD CONSTRAINT synthetic_failure CHECK (idempotency_key='never')")
        db.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        ingest(pg_store, txt(line()))
    assert counts(pg_store) == (0, 0, 0)


def test_tenant_rfc_direction_and_period_reads_are_isolated(pg_store):
    key = ingest(pg_store, txt(line()))['package_key']
    other = scope(tenant_id='tenant-b')
    assert pg_store.read_package(key, other, **(AUTH | {'authenticated_tenant': 'tenant-b'})) is None
    assert latest(pg_store, other, authenticated_tenant='tenant-b') == []
    assert latest(pg_store, scope(direction='recibido')) == []
    with pytest.raises(ValueError):
        ingest(pg_store, txt(line()), authenticated_tenant='tenant-b')
    with pytest.raises(ValueError):
        ingest(pg_store, txt(line()), authorized_company=('foreign', 'AAA010101AAA'))
    assert counts(pg_store) == (1, 1, 1)


def test_unknown_objects_block_migration_rollback(pg_store):
    with pg_store._connection() as db:
        db.execute('CREATE TABLE sentinel (id INTEGER)')
        db.commit()
    with pytest.raises(ValueError): pg_store.migrate_down()
    # Remove only this test's known table, then the fixture can reverse its schema.
    with psycopg.connect(pg_store.dsn) as db:
        db.execute(sql.SQL('DROP TABLE {}.sentinel').format(sql.Identifier(pg_store.schema)))
    assert counts(pg_store) == (0, 0, 0)


@pytest.mark.parametrize('dsn,schema', [
    ('postgresql://user:secret@production.neon.tech/sat_metadata_test', 'sat_md_test_'+'a'*32),
    ('postgresql://user@127.0.0.1/production', 'sat_md_test_'+'a'*32),
    ('postgresql://user@127.0.0.1/sat_metadata_test?host=production', 'sat_md_test_'+'a'*32),
    ('postgresql://user@127.0.0.1/sat_metadata_test', 'public'),
])
def test_production_targets_rejected_before_connection(dsn, schema):
    with pytest.raises(ValueError): assert_test_target(dsn, schema)
