"""Operator-only disposable import. Does not load FastAPI or environment secrets."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schemas.sat_metadata import SyncScope
from app.services.sat_metadata_import import MAX_BYTES
from app.services.sat_metadata_test_store import DisposableMetadataStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--scope', required=True, type=Path)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--disposable-root', required=True, type=Path)
    parser.add_argument('--observed-at', required=True)
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--empresa-id', required=True)
    parser.add_argument('--empresa-rfc', required=True)
    args = parser.parse_args()
    if args.package.stat().st_size > MAX_BYTES or args.scope.stat().st_size > 4096:
        parser.error('Input exceeds offline limits')
    scope = SyncScope.model_validate_json(args.scope.read_text(encoding='utf-8'))
    at = datetime.fromisoformat(args.observed_at.replace('Z', '+00:00'))
    if (scope.tenant_id != args.tenant or (scope.empresa_id, scope.empresa_rfc) != (args.empresa_id, args.empresa_rfc)
            or at.tzinfo is None or at.utcoffset() is None):
        parser.error('Explicit trusted context and observation timezone are required')
    store = DisposableMetadataStore(args.database, disposable_root=args.disposable_root)
    result = store.import_package(args.package.read_bytes(), scope, at,
        authenticated_tenant=args.tenant, authorized_company=(args.empresa_id, args.empresa_rfc))
    # No RFC, names, amounts, source rows or credentials in console output.
    print(json.dumps({key: result[key] for key in (
        'package_key', 'status', 'coverage', 'replayed', 'stored_observations', 'issues')}))
    return 0 if result['status'] == 'validated' else 2


if __name__ == '__main__':
    raise SystemExit(main())
