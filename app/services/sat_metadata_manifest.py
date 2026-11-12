"""Read-only assembly of explicitly declared offline packages, not SAT period coverage."""
from typing import Literal
from pydantic import Field
from app.schemas.sat_metadata import Contract, SyncScope
from app.services.sat_metadata_import import digest, encoded


class ExpectedPackage(Contract):
    package_key: str = Field(pattern=r'^[a-f0-9]{64}$')
    package_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_rows: int = Field(ge=0, le=1000, strict=True)
    unique_rows: int = Field(ge=0, le=1000, strict=True)


class PackageManifest(Contract):
    version: Literal['0.1.0'] = '0.1.0'
    scope: SyncScope
    packages: tuple[ExpectedPackage, ...] = Field(min_length=1, max_length=10)


def assemble_packages(store, manifest: PackageManifest, *, authenticated_tenant: str,
                      authorized_company: tuple[str, str]) -> dict:
    """Verify persisted receipts; never auto-discover extra packages or publish a batch.

    Completeness applies only to the declared package set. SAT request completion,
    coverage of a period and CIMA delivery remain separate, unproved facts.
    """
    scope = manifest.scope
    if scope.tenant_id != authenticated_tenant or (scope.empresa_id, scope.empresa_rfc) != authorized_company:
        raise ValueError('Unauthorized tenant/company scope')
    expected = manifest.packages
    if len({item.package_key for item in expected}) != len(expected):
        raise ValueError('Duplicate package declaration')
    if len({item.package_sha256 for item in expected}) != len(expected):
        raise ValueError('Duplicate package content declaration')
    records, issues, sources = {}, [], {}
    for item in sorted(expected, key=lambda p: p.package_key):
        result = store.read_package(item.package_key, scope, authenticated_tenant=authenticated_tenant,
                                    authorized_company=authorized_company)
        if result is None:
            issues.append({'package_key': item.package_key, 'code': 'missing_package'})
            continue
        if result['status'] != 'validated' or result['envelope'] is None:
            issues.append({'package_key': item.package_key, 'code': 'rejected_package'})
            continue
        actual = result['manifest']
        if (actual['package_sha256'] != item.package_sha256 or actual['source_rows'] != item.source_rows
                or actual['unique_rows'] != item.unique_rows):
            issues.append({'package_key': item.package_key, 'code': 'manifest_mismatch'})
            continue
        for row in result['envelope']['records']:
            uuid = row['cfdi_uuid']
            # Overlap must agree on all fiscal fields; retain all source links separately.
            semantic = {k: v for k, v in row.items() if k not in {
                'raw_ref', 'raw_sha256', 'observed_at', 'idempotency_key',
            }}
            if uuid in records and records[uuid]['semantic'] != semantic:
                issues.append({'package_key': item.package_key, 'code': 'conflicting_uuid'})
            else:
                records.setdefault(uuid, {'semantic': semantic, 'record': row})
            sources.setdefault(uuid, []).append({'package_key': item.package_key,
                                                 'raw_ref': row['raw_ref'], 'raw_sha256': row['raw_sha256']})
    if len(records) > 1000:
        issues.append({'code': 'partition_limit'})
    ready = not issues
    canonical = manifest.model_dump(mode='json')
    canonical['packages'] = sorted(canonical['packages'], key=lambda p: p['package_key'])
    return {'manifest_sha256': digest(encoded(canonical).encode()),
            'package_set_status': 'complete' if ready else 'blocked',
            'coverage': 'partial', 'expected_packages': len(expected),
            'unique_documents': len(records) if ready else 0, 'issues': issues,
            'records': [records[k]['record'] for k in sorted(records)] if ready else [],
            'provenance': sources if ready else {}}
