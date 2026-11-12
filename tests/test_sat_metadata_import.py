"""Synthetic input, temporary real SQLite, independent SQL and two connections."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from threading import Barrier
from zipfile import ZipFile, ZipInfo

import pytest
from app.schemas.sat_metadata import SyncScope
from app.services.sat_metadata_import import HEADERS, MAX_BYTES, parse_package
from app.services.sat_metadata_test_store import DisposableMetadataStore

AT = datetime(2026, 11, 12, 4, tzinfo=timezone.utc)
AUTH = dict(authenticated_tenant='tenant-a', authorized_company=('company-a', 'AAA010101AAA'))


def scope(**updates):
    return SyncScope(**({'tenant_id': 'tenant-a', 'empresa_id': 'company-a',
                         'empresa_rfc': 'AAA010101AAA', 'direction': 'emitido',
                         'period_start': '2026-10-01', 'period_end': '2026-10-31'} | updates))


def line(**updates):
    fields = dict(zip(HEADERS, [
        '22222222-2222-4222-8222-222222222222', 'AAA010101AAA', 'SYNTHETIC EMITTER',
        'BBB010101BBB', 'SYNTHETIC RECEIVER', 'CCC010101CCC',
        '2026-10-10 12:00:00', '2026-10-10 12:01:00', '116.00', 'I', '1', '',
    ]))
    fields.update(updates)
    return '~'.join(fields[k] for k in HEADERS)


def txt(*lines, bom=False):
    raw = ('~'.join(HEADERS) + '\r\n' + '\r\n'.join(lines) + '\r\n').encode()
    return (b'\xef\xbb\xbf' if bom else b'') + raw


def zipped(entries):
    out = BytesIO()
    with ZipFile(out, 'w') as archive:
        for name, raw in entries:
            archive.writestr(name, raw)
    return out.getvalue()


@pytest.fixture
def store(tmp_path):
    return DisposableMetadataStore(tmp_path/'fixture.sat-metadata-test.sqlite3', disposable_root=tmp_path)


def ingest(store, raw, at=AT, selected=None, **auth):
    return store.import_package(raw, selected or scope(), at, **(AUTH | auth))


def counts(store):
    with sqlite3.connect(store.path) as db:
        return tuple(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                     for table in ('packages', 'documents', 'observations'))


def latest(store, selected=None, **auth):
    return store.latest_documents(selected or scope(), **(AUTH | auth))


def test_bom_zip_manifest_raw_preserved_and_replay_after_reopen(store, tmp_path):
    raw = zipped([('metadata.txt', txt(line(), bom=True))])
    first = ingest(store, raw)
    assert first['status'] == 'validated' and first['coverage'] == 'partial'
    assert first['envelope']['records'][0]['total'] == '116.00'
    reopened = DisposableMetadataStore(store.path, disposable_root=tmp_path)
    replay = ingest(reopened, raw, AT.replace(hour=5))
    assert replay == first | {'replayed': True}
    assert counts(store) == (1, 1, 1)
    with sqlite3.connect(store.path) as db:
        original, manifest = db.execute('SELECT raw, manifest FROM packages').fetchone()
        assert original == raw
        assert json.loads(manifest)['unique_rows'] == 1


def test_identical_rows_collapse_but_conflict_rejects_entire_package(store):
    accepted = ingest(store, txt(line(), line()))
    assert accepted['manifest']['identical_duplicates'] == 1
    bad = ingest(store, txt(line(), line(Monto='117.00')))
    assert bad['status'] == 'rejected' and bad['envelope'] is None
    assert bad['issues'][0]['code'] == 'conflicting_uuid'
    assert counts(store) == (2, 1, 1)


def test_any_invalid_row_rejects_all_rows_and_persists_only_evidence(store):
    bad = ingest(store, txt(line(), line(Uuid='33333333-3333-4333-8333-333333333333', Monto='NaN')))
    assert bad['issues'] == [{'entry': 1, 'line': 3, 'code': 'invalid_row_or_scope'}]
    assert counts(store) == (1, 0, 0)
    assert b'NaN' not in json.dumps(bad['issues']).encode()


def test_later_status_appends_and_late_old_package_does_not_regress(store):
    ingest(store, txt(line()))
    cancel = txt(line(Estatus='0', FechaCancelacion='2026-10-20 12:00:00'))
    assert ingest(store, cancel, AT.replace(hour=6))['status'] == 'validated'
    # Different bytes prove a late historical observation; same document facts.
    assert ingest(store, txt(line(), bom=True), AT.replace(hour=3))['status'] == 'validated'
    assert latest(store)[0]['estatus_fiscal'] == 'cancelado'
    assert counts(store) == (3, 1, 3)


def test_stored_uuid_money_conflict_blocks_every_new_row(store):
    ingest(store, txt(line()))
    result = ingest(store, txt(line(Monto='999'), line(Uuid='33333333-3333-4333-8333-333333333333')),
                    AT.replace(hour=5))
    assert result['issues'][0]['code'] == 'stored_uuid_conflict'
    assert counts(store) == (2, 1, 1)
    assert latest(store)[0]['total'] == '116.00'


def test_same_asof_conflicting_state_is_not_arbitrarily_selected(store):
    ingest(store, txt(line()))
    assert ingest(store, txt(line(Estatus='0')), AT)['status'] == 'rejected'
    assert latest(store)[0]['estatus_fiscal'] == 'vigente'


def test_two_connections_import_same_package_once(store, tmp_path):
    other = DisposableMetadataStore(store.path, disposable_root=tmp_path)
    barrier = Barrier(2)
    def import_together(target):
        barrier.wait(timeout=5)
        return ingest(target, txt(line()))
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(import_together, [store, other]))
    assert sorted(r['replayed'] for r in results) == [False, True]
    assert counts(store) == (1, 1, 1)


def test_overlapping_period_same_observation_is_not_inserted_twice(store):
    raw = txt(line())
    ingest(store, raw)
    second = ingest(store, raw, selected=scope(period_start='2026-10-05', period_end='2026-10-20'))
    assert second['status'] == 'validated' and second['stored_observations'] == 0
    assert counts(store) == (2, 1, 1)


def test_wrong_auth_rejected_before_writes_and_cross_scope_reads_return_none(store):
    with pytest.raises(ValueError): ingest(store, txt(line()), authenticated_tenant='tenant-b')
    with pytest.raises(ValueError): ingest(store, txt(line()), authorized_company=('other', 'AAA010101AAA'))
    assert counts(store) == (0, 0, 0)
    key = ingest(store, txt(line()))['package_key']
    b = scope(tenant_id='tenant-b')
    assert store.read_package(key, b, **(AUTH | {'authenticated_tenant': 'tenant-b'})) is None
    assert latest(store, b, authenticated_tenant='tenant-b') == []
    assert ingest(store, txt(line()), selected=b, authenticated_tenant='tenant-b')['status'] == 'validated'
    assert counts(store) == (2, 2, 2)


def test_rfc_direction_period_and_same_uuid_across_companies(store):
    with pytest.raises(ValueError): ingest(store, txt(line()), selected=scope(empresa_rfc='BBB010101BBB'))
    assert ingest(store, txt(line()), selected=scope(direction='recibido'))['status'] == 'rejected'
    assert ingest(store, txt(line(FechaEmision='2026-09-30 12:00:00')))['status'] == 'rejected'
    ingest(store, txt(line()))
    received = scope(empresa_id='company-b', empresa_rfc='BBB010101BBB', direction='recibido')
    auth = {'authorized_company': ('company-b', 'BBB010101BBB')}
    assert ingest(store, txt(line()), selected=received, **auth)['status'] == 'validated'
    assert len(latest(store, received, **auth)) == 1
    assert latest(store, scope(direction='recibido')) == []
    assert latest(store, scope(period_start='2026-11-01', period_end='2026-11-30')) == []


def test_self_invoice_has_one_document_two_directional_observations(store):
    raw = txt(line(RfcReceptor='AAA010101AAA'))
    ingest(store, raw)
    assert ingest(store, raw, selected=scope(direction='recibido'))['status'] == 'validated'
    assert counts(store) == (2, 1, 2)
    assert latest(store)[0]['direccion'] == 'emitido'
    assert latest(store, scope(direction='recibido'))[0]['direccion'] == 'recibido'


@pytest.mark.parametrize('raw,code', [
    (b'not~a~header\n', 'unsupported_header'), (b'PKbroken', 'invalid_zip'),
    (txt(line(Monto='116,00')), 'invalid_row_or_scope'),
    (txt(line(Estatus='2')), 'invalid_row_or_scope'),
    (txt(line(FechaEmision='10/10/2026')), 'invalid_row_or_scope'),
    (txt(line()).replace(b'SYNTHETIC EMITTER', b'NAME~INJECTED'), 'column_count'),
    (txt(line()).replace(b'SYNTHETIC EMITTER', b'NAME\xff'), 'unsupported_encoding'),
])
def test_unknown_formats_fail_closed(store, raw, code):
    result = ingest(store, raw)
    assert result['status'] == 'rejected' and result['issues'][0]['code'] == code
    assert counts(store) == (1, 0, 0)


@pytest.mark.parametrize('name', ['../metadata.txt', '/metadata.txt', 'a/metadata.txt',
                                  'a\\metadata.txt', 'metadata.xml', 'C:metadata.txt'])
def test_unsafe_zip_names_never_extract(store, name):
    assert ingest(store, zipped([(name, txt(line()))]))['status'] == 'rejected'
    assert counts(store) == (1, 0, 0)


def test_zip_symlink_duplicate_name_and_declared_bomb_rejected(store):
    link = ZipInfo('metadata.txt')
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert ingest(store, zipped([(link, b'target')]))['status'] == 'rejected'
    assert ingest(store, zipped([('a.txt', txt(line())), ('A.txt', txt(line()))]))['status'] == 'rejected'
    out = BytesIO()
    with ZipFile(out, 'w', compression=8) as archive: archive.writestr('metadata.txt', b'0' * MAX_BYTES)
    assert ingest(store, out.getvalue())['status'] == 'rejected'
    assert counts(store) == (3, 0, 0)


def test_limits_and_header_only_file_remain_partial(store):
    with pytest.raises(ValueError): ingest(store, b'X'*(MAX_BYTES+1))
    result = ingest(store, ('~'.join(HEADERS)+'\n').encode())
    assert result['status'] == 'validated' and result['coverage'] == 'partial'
    assert result['stored_observations'] == 0
    too_many = ingest(store, txt(*([line()]*1001)))
    assert any(item['code'] == 'row_limit' for item in too_many['issues'])


def test_integrity_tampering_blocks_replay(store):
    key = ingest(store, txt(line()))['package_key']
    with sqlite3.connect(store.path) as db: db.execute("UPDATE packages SET raw=X'00'")
    with pytest.raises(ValueError, match='integrity'):
        store.read_package(key, scope(), **AUTH)
    with pytest.raises(ValueError, match='integrity'): ingest(store, txt(line()))


def test_foreign_database_and_symlink_are_rejected_without_mutation(tmp_path):
    foreign = tmp_path/'foreign.sat-metadata-test.sqlite3'
    with sqlite3.connect(foreign) as db: db.execute('CREATE TABLE sentinel (id INTEGER)')
    before = foreign.read_bytes()
    with pytest.raises(ValueError): DisposableMetadataStore(foreign, disposable_root=tmp_path)
    assert foreign.read_bytes() == before
    link = tmp_path/'link.sat-metadata-test.sqlite3'
    link.symlink_to(foreign)
    with pytest.raises(ValueError): DisposableMetadataStore(link, disposable_root=tmp_path)
    with pytest.raises(ValueError): DisposableMetadataStore(tmp_path/'production.sqlite3', disposable_root=tmp_path)


def test_database_failure_rolls_back_package_document_and_observation(store):
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER reject_observation BEFORE INSERT ON observations BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError): ingest(store, txt(line()))
    assert counts(store) == (0, 0, 0)


def test_cli_roundtrip_with_synthetic_local_file(tmp_path):
    package = tmp_path/'synthetic.txt'
    package.write_bytes(txt(line()))
    selected = tmp_path/'scope.json'
    selected.write_text(scope().model_dump_json())
    database = tmp_path/'cli.sat-metadata-test.sqlite3'
    command = [sys.executable, 'scripts/import_sat_metadata_offline.py',
               '--package', str(package), '--scope', str(selected),
               '--database', str(database), '--disposable-root', str(tmp_path),
               '--observed-at', AT.isoformat(), '--tenant', 'tenant-a',
               '--empresa-id', 'company-a', '--empresa-rfc', 'AAA010101AAA']
    for replayed in (False, True):
        result = subprocess.run(command, text=True, capture_output=True, check=True)
        report = json.loads(result.stdout)
        assert report['status'] == 'validated' and report['replayed'] == replayed
        assert '116.00' not in result.stdout and 'AAA010101AAA' not in result.stdout
