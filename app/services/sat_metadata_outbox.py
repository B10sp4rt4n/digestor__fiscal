"""Explicit lab outbox. No worker startup, HTTP, credentials or production targets."""
from contextlib import contextmanager
import json
import re
from uuid import UUID, uuid4
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from app.services.sat_metadata_postgres import assert_test_target
from app.services.sat_metadata_import import digest, encoded

VERSION = 'sat-outbox-test-0.2.0'


class DisposableMetadataOutbox:
    def __init__(self, dsn, *, schema):
        if not re.fullmatch(r'sat_out_test_[a-f0-9]{32}', schema):
            raise ValueError('Generated outbox test schema required')
        assert_test_target(dsn, schema.replace('sat_out_test_', 'sat_md_test_', 1))
        self.dsn, self.schema = dsn, schema
        self.lock = int(digest(schema.encode())[:15], 16)
        with self.connection(initializing=True) as db:
            exists = db.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s', (schema,)).fetchone()
            if exists:
                self._owned(db)
            else:
                db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
                self._path(db)
                db.execute('CREATE TABLE outbox_meta (version TEXT PRIMARY KEY)')
                db.execute('INSERT INTO outbox_meta VALUES (%s)', (VERSION,))
                db.execute('CREATE TABLE deliveries (id TEXT PRIMARY KEY, owner TEXT NOT NULL, '
                    'payload TEXT NOT NULL, payload_hash TEXT NOT NULL, status TEXT NOT NULL, '
                    'attempts INTEGER NOT NULL DEFAULT 0, token TEXT, lease_until TIMESTAMPTZ, '
                    'next_attempt TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), '
                    'ack TEXT, ack_hash TEXT, last_error TEXT, evidence TEXT, evidence_hash TEXT)')

    def _path(self, db):
        db.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(self.schema)))

    def _owned(self, db):
        self._path(db)
        names = db.execute('SELECT tablename FROM pg_tables WHERE schemaname=%s', (self.schema,)).fetchall()
        if {r['tablename'] for r in names} != {'outbox_meta', 'deliveries'}:
            raise ValueError('Foreign outbox schema')
        if db.execute('SELECT version FROM outbox_meta').fetchall() != [{'version': VERSION}]:
            raise ValueError('Unknown outbox version')

    @contextmanager
    def connection(self, initializing=False):
        assert_test_target(self.dsn, self.schema.replace('sat_out_test_', 'sat_md_test_', 1))
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)', (self.lock,))
            if not initializing:
                self._owned(db)
            yield db

    @staticmethod
    def _owner(scope, auth):
        if (scope.tenant_id != auth['authenticated_tenant']
                or (scope.empresa_id, scope.empresa_rfc) != auth['authorized_company']):
            raise ValueError('Unauthorized scope')
        return encoded([scope.tenant_id, scope.empresa_id, scope.empresa_rfc])

    def enqueue_package(self, store, package_key, scope, **auth):
        result = store.read_package(package_key, scope, **auth)
        if not result or result['status'] != 'validated' or not result['envelope']:
            raise ValueError('Validated persisted package required')
        return self._enqueue_verified(result['envelope'], scope, **auth)

    def enqueue_manifest(self, store, manifest, **auth):
        from uuid import NAMESPACE_URL, uuid5
        from app.services.sat_metadata_manifest import assemble_packages
        assembled = assemble_packages(store, manifest, **auth)
        if assembled['package_set_status'] != 'complete':
            raise ValueError('Incomplete or conflicting declared package set')
        sha = assembled['manifest_sha256']
        body = {'dataset': 'sat_metadata_observation', 'schema_version': '0.1.0',
            'batch_id': str(uuid5(NAMESPACE_URL, 'manifest:'+sha)),
            'scope': manifest.scope.model_dump(mode='json'), 'coverage': 'partial', 'phase': 'validated',
            'manifest_ref': 'local-manifest-set:'+sha, 'manifest_sha256': sha,
            'expected_count': assembled['unique_documents'], 'records': assembled['records']}
        declaration = manifest.model_dump(mode='json')
        declaration['packages'] = sorted(declaration['packages'], key=lambda p: p['package_key'])
        evidence = {'manifest': declaration, 'provenance': assembled['provenance']}
        return self._enqueue_verified(body, manifest.scope, evidence=evidence, **auth)

    def _enqueue_verified(self, envelope, scope, *, evidence=None, **auth):
        owner = self._owner(scope, auth)
        payload = encoded(envelope)
        payload_hash = digest(payload.encode())
        evidence_text = encoded(evidence) if evidence is not None else None
        evidence_hash = digest(evidence_text.encode()) if evidence_text else None
        delivery_id = digest(encoded([owner, envelope['batch_id']]).encode())
        with self.connection() as db:
            row = db.execute('SELECT * FROM deliveries WHERE id=%s AND owner=%s', (delivery_id, owner)).fetchone()
            if row:
                if (row['payload_hash'] != payload_hash or digest(row['payload'].encode()) != row['payload_hash']
                        or row['evidence_hash'] != evidence_hash):
                    raise ValueError('Immutable delivery conflict')
            else:
                db.execute("INSERT INTO deliveries (id,owner,payload,payload_hash,status,evidence,evidence_hash) VALUES (%s,%s,%s,%s,'pending',%s,%s)",
                           (delivery_id, owner, payload, payload_hash, evidence_text, evidence_hash))
        return delivery_id

    def read(self, delivery_id, scope, **auth):
        owner = self._owner(scope, auth)
        with self.connection() as db:
            row = db.execute('SELECT * FROM deliveries WHERE id=%s AND owner=%s', (delivery_id, owner)).fetchone()
            if row and (digest(row['payload'].encode()) != row['payload_hash']
                        or row['ack'] is not None and digest(row['ack'].encode()) != row['ack_hash']
                        or row['evidence'] is not None and digest(row['evidence'].encode()) != row['evidence_hash']):
                raise ValueError('Outbox integrity mismatch')
            return row

    def claim(self, delivery_id, scope, **auth):
        owner = self._owner(scope, auth)
        self.read(delivery_id, scope, **auth)
        token = str(uuid4())
        with self.connection() as db:
            db.execute("UPDATE deliveries SET status='blocked',last_error='attempt_limit',token=NULL,lease_until=NULL "
                "WHERE id=%s AND owner=%s AND attempts>=5 AND status='inflight' AND lease_until<=clock_timestamp()",
                (delivery_id, owner))
            return db.execute("UPDATE deliveries SET status='inflight', token=%s, "
                "lease_until=clock_timestamp()+interval '60 seconds', attempts=attempts+1 "
                "WHERE id=%s AND owner=%s AND attempts<5 AND next_attempt<=clock_timestamp() "
                "AND (status IN ('pending','retry') OR (status='inflight' AND lease_until<=clock_timestamp())) RETURNING *",
                (token, delivery_id, owner)).fetchone()

    def dispatch(self, delivery_id, scope, transport, **auth):
        row = self.claim(delivery_id, scope, **auth)
        if not row:
            return self.read(delivery_id, scope, **auth)
        body = json.loads(row['payload'])
        status, ack_text, error = 'retry', None, 'transport_unknown'
        try:
            ack = transport(body)  # No database lock or transaction over the external call.
        except Exception:
            pass  # Preserve only a fixed error code, never provider exception contents.
        else:
            try:
                UUID(ack['receipt_id'])
                expected = {'status': 'accepted', 'batch_id': body['batch_id'],
                    'dataset': body['dataset'], 'schema_version': body['schema_version'],
                    'tenant_id': scope.tenant_id, 'empresa_id': scope.empresa_id,
                    'empresa_rfc': scope.empresa_rfc, 'payload_sha256': row['payload_hash'], 'coverage': 'partial'}
                if any(ack.get(k) != v for k, v in expected.items()):
                    raise ValueError('Mismatched acknowledgment')
                if (type(ack['received_records']) is not int or ack['received_records'] != len(body['records'])
                        or type(ack['new_records']) is not int or not 0 <= ack['new_records'] <= ack['received_records']):
                    raise ValueError('Mismatched count')
                ack_text, status, error = encoded(ack), 'acknowledged', None
            except (KeyError, TypeError, ValueError, AttributeError):
                status, error = 'blocked', 'invalid_ack'
        with self.connection() as db:
            # A late worker cannot overwrite a newer claim or confirm an expired reservation.
            db.execute("UPDATE deliveries SET status=%s,ack=%s,ack_hash=%s,last_error=%s,token=NULL,lease_until=NULL, "
                "next_attempt=clock_timestamp()+(%s * interval '1 second') "
                "WHERE id=%s AND owner=%s AND token=%s AND lease_until>clock_timestamp()",
                ('blocked' if status == 'retry' and row['attempts'] >= 5 else status,
                 ack_text, digest(ack_text.encode()) if ack_text else None, error,
                 min(2**row['attempts'], 60), delivery_id, row['owner'], row['token']))
        return self.read(delivery_id, scope, **auth)

    def reverse(self):
        with self.connection() as db:
            db.execute('DROP TABLE deliveries')
            db.execute('DROP TABLE outbox_meta')
            db.execute(sql.SQL('DROP SCHEMA {}').format(sql.Identifier(self.schema)))
