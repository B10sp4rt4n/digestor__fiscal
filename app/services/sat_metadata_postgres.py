"""Guarded PostgreSQL staging proof. No production DSN or Alembic registration."""
from contextlib import contextmanager
import re
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from app.services.sat_metadata_staging import StagingOperations
from app.services.sat_metadata_import import digest

VERSION = 'sat-metadata-staging-0.1.0'
TABLES = {'store_meta', 'packages', 'documents', 'observations'}
DDL = (
    'CREATE TABLE packages (package_key TEXT PRIMARY KEY, scope TEXT NOT NULL, '
    'raw BYTEA NOT NULL, raw_hash TEXT NOT NULL, manifest TEXT NOT NULL, manifest_hash TEXT NOT NULL, '
    'result TEXT NOT NULL, result_hash TEXT NOT NULL)',
    'CREATE TABLE documents (identity TEXT PRIMARY KEY, scope_owner TEXT NOT NULL, '
    'facts_hash TEXT NOT NULL, observed_at TEXT NOT NULL, state_hash TEXT NOT NULL, '
    'payload TEXT NOT NULL, payload_hash TEXT NOT NULL)',
    'CREATE TABLE observations (idempotency_key TEXT PRIMARY KEY, '
    'identity TEXT NOT NULL REFERENCES documents(identity), package_key TEXT NOT NULL '
    'REFERENCES packages(package_key), payload TEXT NOT NULL, payload_hash TEXT NOT NULL)',
)


def assert_test_target(dsn: str, schema: str):
    target = urlparse(dsn)
    if (target.scheme not in ('postgresql', 'postgres')
            or target.hostname not in ('127.0.0.1', 'localhost', '::1')
            or target.path != '/sat_metadata_test' or target.query or target.fragment
            or not re.fullmatch(r'sat_md_test_[a-f0-9]{32}', schema)):
        raise ValueError('Only loopback sat_metadata_test and an isolated generated schema are allowed')


class PostgresSession:
    def __init__(self, db, lock_id):
        self.db, self.lock_id = db, lock_id

    def execute(self, query, parameters=()):
        return self.db.execute(query, parameters)

    def begin_serialized(self):
        # Deliberately serializes this disposable staging schema, like SQLite.
        self.db.execute('SELECT pg_advisory_xact_lock(%s)', (self.lock_id,))

    def commit(self):
        self.db.commit()


class DisposablePostgresMetadataStore(StagingOperations):
    def __init__(self, dsn: str, *, schema: str):
        assert_test_target(dsn, schema)
        self.dsn, self.schema = dsn, schema
        self.lock_id = int(digest(schema.encode())[:15], 16)
        self.migrate_up()

    def _set_path(self, db):
        db.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(self.schema)))

    def _assert_owned(self, db):
        rows = db.execute('SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname=%s',
                          (self.schema,)).fetchall()
        if {r['tablename'] for r in rows} != TABLES:
            raise ValueError('Unexpected or foreign staging schema')
        versions = db.execute('SELECT version FROM store_meta').fetchall()
        if versions != [{'version': VERSION}]:
            raise ValueError('Unknown staging schema version')

    def migrate_up(self):
        assert_test_target(self.dsn, self.schema)
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)', (self.lock_id,))
            exists = db.execute('SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname=%s',
                                (self.schema,)).fetchone()
            if exists:
                self._set_path(db)
                self._assert_owned(db)
                return
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
            self._set_path(db)
            db.execute('CREATE TABLE store_meta (version TEXT PRIMARY KEY)')
            db.execute('INSERT INTO store_meta VALUES (%s)', (VERSION,))
            for statement in DDL:
                db.execute(statement)

    def migrate_down(self):
        assert_test_target(self.dsn, self.schema)
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)', (self.lock_id,))
            exists = db.execute('SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname=%s',
                                (self.schema,)).fetchone()
            if not exists:
                return
            self._set_path(db)
            self._assert_owned(db)
            # No CASCADE: unknown dependencies block rollback instead of being destroyed.
            for table in ('observations', 'documents', 'packages', 'store_meta'):
                db.execute(sql.SQL('DROP TABLE {}').format(sql.Identifier(table)))
            db.execute(sql.SQL('DROP SCHEMA {}').format(sql.Identifier(self.schema)))

    @contextmanager
    def _connection(self):
        assert_test_target(self.dsn, self.schema)
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            self._set_path(db)
            self._assert_owned(db)
            yield PostgresSession(db, self.lock_id)
