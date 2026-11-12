"""Use actual persisted package evidence, not a simulated store."""
import sqlite3
from uuid import uuid4
import pytest
from app.services.sat_metadata_manifest import ExpectedPackage, PackageManifest, assemble_packages
from tests.test_sat_metadata_import import AUTH, AT, scope, line, txt, ingest, store
from tests.test_sat_metadata_postgres import pg_store


def declaration(result):
    return ExpectedPackage(package_key=result['package_key'],
        **{k: result['manifest'][k] for k in ('package_sha256', 'source_rows', 'unique_rows')})


def assemble(store, packages, **auth):
    return assemble_packages(store, PackageManifest(scope=scope(), packages=packages), **(AUTH | auth))


def test_two_packages_complete_only_the_declared_set(store):
    a = ingest(store, txt(line()))
    b = ingest(store, txt(line(Uuid=str(uuid4()))))
    result = assemble(store, [declaration(a), declaration(b)])
    assert result['package_set_status'] == 'complete' and result['coverage'] == 'partial'
    assert result['unique_documents'] == 2 and len(result['records']) == 2
    assert assemble(store, [declaration(b), declaration(a)]) == result
    assert assemble(store, [declaration(a), declaration(b)]) == result


def test_missing_package_blocks_every_output_record(store):
    a = ingest(store, txt(line()))
    missing = declaration(a).model_copy(update={'package_key': 'f'*64, 'package_sha256': 'e'*64})
    result = assemble(store, [declaration(a), missing])
    assert result['package_set_status'] == 'blocked' and not result['records']
    assert result['issues'][0]['code'] == 'missing_package'


@pytest.mark.parametrize('field,value', [('package_sha256', 'f'*64), ('source_rows', 2), ('unique_rows', 2)])
def test_hash_and_count_mismatch_blocks_manifest(store, field, value):
    a = ingest(store, txt(line()))
    wrong = declaration(a).model_copy(update={field: value})
    assert assemble(store, [wrong])['issues'][0]['code'] == 'manifest_mismatch'


def test_overlapping_identical_document_keeps_both_source_links(store):
    a = ingest(store, txt(line()))
    b = ingest(store, txt(line(), bom=True))
    result = assemble(store, [declaration(a), declaration(b)])
    assert result['unique_documents'] == 1
    assert len(next(iter(result['provenance'].values()))) == 2


def test_different_state_in_same_declared_snapshot_is_a_conflict(store):
    a = ingest(store, txt(line()))
    b = ingest(store, txt(line(Estatus='0')), AT.replace(hour=6))
    assert assemble(store, [declaration(a), declaration(b)])['issues'][0]['code'] == 'conflicting_uuid'


def test_rejected_package_is_not_accepted_as_complete(store):
    rejected = ingest(store, txt(line(Monto='NaN')))
    assert assemble(store, [declaration(rejected)])['issues'][0]['code'] == 'rejected_package'


def test_scope_auth_and_duplicate_declarations_are_blocked(store):
    a = declaration(ingest(store, txt(line())))
    with pytest.raises(ValueError): assemble(store, [a], authenticated_tenant='foreign')
    with pytest.raises(ValueError): assemble(store, [a, a])
    other = a.model_copy(update={'package_key': 'f'*64})
    with pytest.raises(ValueError): assemble(store, [a, other])


def test_integrity_is_checked_again_and_no_documents_are_written(store):
    a = ingest(store, txt(line()))
    with sqlite3.connect(store.path) as db:
        before = db.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
        db.execute("UPDATE packages SET raw=X'00'")
    with pytest.raises(ValueError, match='integrity'): assemble(store, [declaration(a)])
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == before


def test_partition_limit_is_checked_across_packages(store):
    a = ingest(store, txt(*(line(Uuid=str(uuid4())) for _ in range(501))))
    b = ingest(store, txt(*(line(Uuid=str(uuid4())) for _ in range(501))))
    result = assemble(store, [declaration(a), declaration(b)])
    assert result['issues'] == [{'code': 'partition_limit'}] and not result['records']


def test_same_assembly_against_postgres(pg_store):
    a = ingest(pg_store, txt(line()))
    b = ingest(pg_store, txt(line(Uuid=str(uuid4()))))
    assert assemble(pg_store, [declaration(a), declaration(b)])['unique_documents'] == 2
