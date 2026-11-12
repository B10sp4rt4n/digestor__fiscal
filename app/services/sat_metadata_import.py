"""Bounded offline TXT/ZIP import. No application, credentials or network imports."""
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import json
import re
import stat
import zlib
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZipFile, BadZipFile

from pydantic import ValidationError
from app.schemas.sat_metadata import MetadataBatch, SatMetadata, SyncScope, prepare_cima_batch

HEADERS = ('Uuid', 'RfcEmisor', 'NombreEmisor', 'RfcReceptor', 'NombreReceptor',
           'RfcPac', 'FechaEmision', 'FechaCertificacionSat', 'Monto',
           'EfectoComprobante', 'Estatus', 'FechaCancelacion')
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000


def digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class PackageError(ValueError):
    pass


def _entries(raw: bytes):
    if not raw or len(raw) > MAX_BYTES:
        raise PackageError('package_size')
    if not raw.startswith(b'PK'):
        return [('metadata.txt', raw)]
    try:
        with ZipFile(BytesIO(raw)) as archive:
            members = archive.infolist()
            if not 1 <= len(members) <= 10:
                raise PackageError('zip_entry_count')
            names, total = set(), 0
            for item in members:
                name = item.filename
                mode = item.external_attr >> 16
                total += item.file_size
                if (PurePosixPath(name).name != name or '\\' in name or ':' in name
                        or not name.lower().endswith('.txt') or name.casefold() in names
                        or item.is_dir() or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                        or item.flag_bits & 1 or total > MAX_BYTES
                        or item.file_size > max(item.compress_size, 1) * 100):
                    raise PackageError('unsafe_zip_entry')
                names.add(name.casefold())
            # No extraction to disk; names never become filesystem paths.
            return [(item.filename, archive.read(item)) for item in members]
    except (BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error):
        raise PackageError('invalid_zip') from None


def _date(value: str):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', value):
        raise ValueError('Unsupported source date')
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')


def parse_package(raw: bytes, scope: SyncScope, observed_at: datetime):
    """Validate all rows before staging. Error reports never contain source values."""
    package_hash = digest(raw)
    issues, rows, manifest_entries = [], {}, []
    source_rows, duplicates = 0, 0

    def issue(entry, line, code):
        if len(issues) < 20:
            issues.append({'entry': entry, 'line': line, 'code': code})

    try:
        entries = _entries(raw)
    except PackageError as error:
        entries = []
        issue(0, 0, str(error))
    for index, (_, data) in enumerate(entries, 1):
        manifest_entries.append({'entry': index, 'sha256': digest(data), 'bytes': len(data)})
        try:
            lines = data.decode('utf-8-sig', errors='strict').splitlines()
        except UnicodeDecodeError:
            issue(index, 0, 'unsupported_encoding')
            continue
        if not lines or lines[0].split('~') != list(HEADERS):
            issue(index, 1, 'unsupported_header')
            continue
        for line_number, line in enumerate(lines[1:], 2):
            if not line.strip():
                issue(index, line_number, 'empty_row')
                continue
            source_rows += 1
            if source_rows > MAX_ROWS:
                issue(index, line_number, 'row_limit')
                break
            fields = line.split('~')
            if len(fields) != len(HEADERS):
                issue(index, line_number, 'column_count')
                continue
            values = dict(zip(HEADERS, fields))
            try:
                row = SatMetadata(
                    cfdi_uuid=values['Uuid'], emisor_rfc=values['RfcEmisor'],
                    receptor_rfc=values['RfcReceptor'], emisor_nombre=values['NombreEmisor'],
                    receptor_nombre=values['NombreReceptor'], rfc_pac=values['RfcPac'],
                    fecha_emision=_date(values['FechaEmision']),
                    fecha_certificacion=_date(values['FechaCertificacionSat']),
                    total=values['Monto'], tipo_comprobante=values['EfectoComprobante'],
                    estatus_fiscal={'1': 'vigente', '0': 'cancelado'}[values['Estatus']],
                    fecha_cancelacion=_date(values['FechaCancelacion']) if values['FechaCancelacion'] else None,
                    observed_at=observed_at, raw_ref=f'local-package:{package_hash}/{index}/{line_number}',
                    raw_sha256=digest(line.encode('utf-8')),
                )
                # Per-row scope check produces an actionable line number.
                MetadataBatch(batch_id=uuid5(NAMESPACE_URL, package_hash), scope=scope,
                              phase='validated', coverage='partial', observations=(row,))
            except (ValueError, KeyError, ValidationError):
                issue(index, line_number, 'invalid_row_or_scope')
                continue
            previous = rows.get(row.cfdi_uuid)
            if previous:
                # Provenance can differ; document data must agree exactly within the package.
                exclude = {'raw_ref', 'raw_sha256'}
                if previous.model_dump(exclude=exclude) != row.model_dump(exclude=exclude):
                    issue(index, line_number, 'conflicting_uuid')
                else:
                    duplicates += 1
            else:
                rows[row.cfdi_uuid] = row
    manifest = {'profile': 'sat-metadata-txt-12-v1', 'package_sha256': package_hash,
                'entries': manifest_entries, 'source_rows': source_rows,
                'unique_rows': len(rows), 'identical_duplicates': duplicates,
                'coverage': 'partial'}
    return tuple(rows.values()), issues, manifest


def prepare_import(raw: bytes, scope: SyncScope, observed_at: datetime, *,
                   authenticated_tenant: str, authorized_company: tuple[str, str]):
    # Authorization precedes parsing or storage. Context is resolved by trusted caller.
    if scope.tenant_id != authenticated_tenant or (scope.empresa_id, scope.empresa_rfc) != authorized_company:
        raise ValueError('Unauthorized tenant/company scope')
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError('Observation requires an explicit timezone')
    rows, issues, manifest = parse_package(raw, scope, observed_at)
    manifest_json = encoded(manifest)
    key = digest(encoded([scope.model_dump(mode='json'), digest(raw)]).encode())
    envelope = None
    if not issues:
        batch = MetadataBatch(batch_id=uuid5(NAMESPACE_URL, key), scope=scope,
                              phase='validated', coverage='partial', observations=rows,
                              manifest_ref=f'local-manifest:{key}',
                              manifest_sha256=digest(manifest_json.encode()), expected_count=len(rows))
        envelope = prepare_cima_batch(batch, authenticated_tenant=authenticated_tenant,
                                      authorized_company=authorized_company)
    return key, envelope, issues, manifest_json
