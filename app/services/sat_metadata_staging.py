"""Shared staging semantics, independent of the database driver and app runtime."""
from datetime import timezone
from decimal import Decimal
import json
from app.schemas.sat_metadata import SyncScope
from app.services.sat_metadata_import import MAX_BYTES, digest, encoded, prepare_import

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


class StagingOperations:
    def import_package(self, raw: bytes, scope: SyncScope, observed_at, *,
                       authenticated_tenant: str, authorized_company: tuple[str, str]):
        if len(raw) > MAX_BYTES:
            raise ValueError('Package size limit exceeded')
        key, envelope, issues, manifest = prepare_import(raw, scope, observed_at,
            authenticated_tenant=authenticated_tenant, authorized_company=authorized_company)
        scope_json = encoded(scope.model_dump(mode='json'))
        at = observed_at.astimezone(timezone.utc).isoformat()
        with self._connection() as db:
            db.begin_serialized()
            existing = db.execute('SELECT * FROM packages WHERE package_key=%s AND scope=%s',
                                  (key, scope_json)).fetchone()
            if existing:
                return {**_verify_package(existing), 'replayed': True}
            updates = []
            for row in envelope['records'] if envelope else []:
                facts_hash, state_hash = _hashes(row)
                old = db.execute('SELECT * FROM documents WHERE identity=%s AND scope_owner=%s',
                                 (row['document_identity'], _owner(scope))).fetchone()
                if old and digest(old['payload'].encode()) != old['payload_hash']:
                    raise ValueError('Staged document integrity mismatch')
                if old and (old['facts_hash'] != facts_hash
                            or old['observed_at'] == at and old['state_hash'] != state_hash):
                    issues.append({'entry': 0, 'line': 0, 'code': 'stored_uuid_conflict'})
                previous_observation = db.execute('SELECT payload, payload_hash FROM observations '
                    'WHERE idempotency_key=%s AND identity=%s',
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
            db.execute('INSERT INTO packages VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                       (key, scope_json, raw, digest(raw), manifest, digest(manifest.encode()),
                        result_text, digest(result_text.encode())))
            if accepted:
                for row, old, facts_hash, state_hash, is_new in updates:
                    payload = encoded(row)
                    if not old:
                        db.execute('INSERT INTO documents VALUES (%s, %s, %s, %s, %s, %s, %s)',
                                   (row['document_identity'], _owner(scope), facts_hash, at,
                                    state_hash, payload, digest(payload.encode())))
                    elif at > old['observed_at']:
                        db.execute('UPDATE documents SET observed_at=%s, state_hash=%s, payload=%s, '
                                   'payload_hash=%s WHERE identity=%s AND scope_owner=%s',
                                   (at, state_hash, payload, digest(payload.encode()),
                                    row['document_identity'], _owner(scope)))
                    if is_new:
                        db.execute('INSERT INTO observations VALUES (%s, %s, %s, %s, %s)',
                                   (row['idempotency_key'], row['document_identity'], key,
                                    payload, digest(payload.encode())))
            db.commit()
            return result

    def read_package(self, key: str, scope: SyncScope, *, authenticated_tenant: str,
                     authorized_company: tuple[str, str]):
        _authorize(scope, authenticated_tenant, authorized_company)
        with self._connection() as db:
            row = db.execute('SELECT * FROM packages WHERE package_key=%s AND scope=%s',
                             (key, encoded(scope.model_dump(mode='json')))).fetchone()
            return _verify_package(row) if row else None

    def latest_documents(self, scope: SyncScope, *, authenticated_tenant: str,
                         authorized_company: tuple[str, str]):
        _authorize(scope, authenticated_tenant, authorized_company)
        with self._connection() as db:
            rows = db.execute('SELECT * FROM documents WHERE scope_owner=%s ORDER BY identity',
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
