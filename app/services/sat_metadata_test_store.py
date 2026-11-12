"""Disposable SQLite staging proof. Not imported by app startup or production APIs."""
from contextlib import contextmanager
from datetime import timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3

from app.schemas.sat_metadata import SyncScope
from app.services.sat_metadata_import import MAX_BYTES, digest, encoded, prepare_import

APPLICATION_ID = 1396790356
DDL = (
    'CREATE TABLE IF NOT EXISTS packages (package_key TEXT PRIMARY KEY, scope TEXT NOT NULL, '
    'raw BLOB NOT NULL, raw_hash TEXT NOT NULL, manifest TEXT NOT NULL, manifest_hash TEXT NOT NULL, '
    'result TEXT NOT NULL, result_hash TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS documents (identity TEXT PRIMARY KEY, scope_owner TEXT NOT NULL, '
    'facts_hash TEXT NOT NULL, observed_at TEXT NOT NULL, state_hash TEXT NOT NULL, '
    'payload TEXT NOT NULL, payload_hash TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS observations (idempotency_key TEXT PRIMARY KEY, '
    'identity TEXT NOT NULL REFERENCES documents(identity), package_key TEXT NOT NULL '
    'REFERENCES packages(package_key), payload TEXT NOT NULL, payload_hash TEXT NOT NULL)',
)


def _authorize(scope, tenant, company):
    if scope.tenant_id != tenant or (scope.empresa_id, scope.empresa_rfc) != company:
        raise ValueError('Unauthorized tenant/company scope')


def _owner(scope):
    return encoded([scope.tenant_id, scope.empresa_id, scope.empresa_rfc])


def _hashes(row):
    facts = {k: v for k, v in row.items() if k not in {
        'raw_ref', 'raw_sha256', 'observed_at', 'estatus_fiscal', 'fecha_cancelacion',
        'document_identity', 'idempotency_key', 'direccion', 'enrichment_required',
    }}
    facts['total'] = format(Decimal(facts['total']).normalize(), 'f')
    return digest(encoded(facts).encode()), digest(encoded([
        row['estatus_fiscal'], row['fecha_cancelacion']]).encode())


def _verify_package(row):
    if (digest(row['raw']) != row['raw_hash'] or digest(row['manifest'].encode()) != row['manifest_hash']
            or digest(row['result'].encode()) != row['result_hash']):
        raise ValueError('Staged package integrity mismatch')
    return json.loads(row['result'])


class DisposableMetadataStore:
    def __init__(self, path: Path, *, disposable_root: Path):
        self.path = Path(path)
        root = Path(disposable_root).resolve(strict=True)
        if (not self.path.is_absolute() or not self.path.name.endswith('.sat-metadata-test.sqlite3')
                or self.path.is_symlink() or not self.path.parent.resolve(strict=True).is_relative_to(root)):
            raise ValueError('An explicit disposable local database path is required')
        created = False
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            created = True
        except FileExistsError:
            pass
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            app_id = db.execute('PRAGMA application_id').fetchone()[0]
            if app_id != APPLICATION_ID and not (created and app_id == 0):
                raise ValueError('Refusing a database not owned by this test store')
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            for statement in DDL:
                db.execute(statement)
            db.commit()

    @contextmanager
    def _connection(self):
        if self.path.is_symlink():
            raise ValueError('Symlink database rejected')
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()  # Uncommitted work rolls back, including an interrupted import.

    def import_package(self, raw: bytes, scope: SyncScope, observed_at, *,
                       authenticated_tenant: str, authorized_company: tuple[str, str]):
        if len(raw) > MAX_BYTES:
            raise ValueError('Package size limit exceeded')
        key, envelope, issues, manifest = prepare_import(raw, scope, observed_at,
            authenticated_tenant=authenticated_tenant, authorized_company=authorized_company)
        scope_json = encoded(scope.model_dump(mode='json'))
        at = observed_at.astimezone(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM packages WHERE package_key=? AND scope=?',
                                  (key, scope_json)).fetchone()
            if existing:
                return {**_verify_package(existing), 'replayed': True}
            updates = []
            for row in envelope['records'] if envelope else []:
                facts_hash, state_hash = _hashes(row)
                old = db.execute('SELECT * FROM documents WHERE identity=? AND scope_owner=?',
                                 (row['document_identity'], _owner(scope))).fetchone()
                if old and digest(old['payload'].encode()) != old['payload_hash']:
                    raise ValueError('Staged document integrity mismatch')
                if old and (old['facts_hash'] != facts_hash
                            or old['observed_at'] == at and old['state_hash'] != state_hash):
                    issues.append({'entry': 0, 'line': 0, 'code': 'stored_uuid_conflict'})
                previous_observation = db.execute('SELECT payload, payload_hash FROM observations '
                    'WHERE idempotency_key=? AND identity=?',
                    (row['idempotency_key'], row['document_identity'])).fetchone()
                if previous_observation and (digest(previous_observation['payload'].encode()) != previous_observation['payload_hash']
                                              or previous_observation['payload'] != encoded(row)):
                    raise ValueError('Staged observation integrity mismatch')
                updates.append((row, old, facts_hash, state_hash, previous_observation is None))
            accepted = not issues
            result = {'package_key': key, 'status': 'validated' if accepted else 'rejected',
                      'coverage': 'partial', 'replayed': False, 'issues': issues[:20],
                      'stored_observations': sum(update[4] for update in updates) if accepted else 0,
                      'manifest': json.loads(manifest), 'envelope': envelope if accepted else None}
            result_text = encoded(result)
            db.execute('INSERT INTO packages VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                       (key, scope_json, raw, digest(raw), manifest, digest(manifest.encode()),
                        result_text, digest(result_text.encode())))
            if accepted:
                for row, old, facts_hash, state_hash, is_new in updates:
                    payload = encoded(row)
                    if not old:
                        db.execute('INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)',
                                   (row['document_identity'], _owner(scope), facts_hash, at,
                                    state_hash, payload, digest(payload.encode())))
                    elif at > old['observed_at']:
                        db.execute('UPDATE documents SET observed_at=?, state_hash=?, payload=?, '
                                   'payload_hash=? WHERE identity=? AND scope_owner=?',
                                   (at, state_hash, payload, digest(payload.encode()),
                                    row['document_identity'], _owner(scope)))
                    if is_new:
                        db.execute('INSERT INTO observations VALUES (?, ?, ?, ?, ?)',
                                   (row['idempotency_key'], row['document_identity'], key,
                                    payload, digest(payload.encode())))
            db.commit()
            return result

    def read_package(self, key: str, scope: SyncScope, *, authenticated_tenant: str,
                     authorized_company: tuple[str, str]):
        _authorize(scope, authenticated_tenant, authorized_company)
        with self._connection() as db:
            row = db.execute('SELECT * FROM packages WHERE package_key=? AND scope=?',
                             (key, encoded(scope.model_dump(mode='json')))).fetchone()
            return _verify_package(row) if row else None

    def latest_documents(self, scope: SyncScope, *, authenticated_tenant: str,
                         authorized_company: tuple[str, str]):
        _authorize(scope, authenticated_tenant, authorized_company)
        with self._connection() as db:
            rows = db.execute('SELECT * FROM documents WHERE scope_owner=? ORDER BY identity',
                              (_owner(scope),)).fetchall()
            result = []
            for row in rows:
                if digest(row['payload'].encode()) != row['payload_hash']:
                    raise ValueError('Staged document integrity mismatch')
                payload = json.loads(row['payload'])
                owner_rfc = payload['emisor_rfc'] if scope.direction == 'emitido' else payload['receptor_rfc']
                issue_date = payload['fecha_emision'][:10]
                if owner_rfc == scope.empresa_rfc and scope.period_start.isoformat() <= issue_date <= scope.period_end.isoformat():
                    result.append({**payload, 'direccion': scope.direction})
            return result
