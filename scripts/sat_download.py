"""Opt-in SAT acquisition staging. Does not timbrar, cancel, or write CIMA.

One local persistent volume and worker. Secrets are files, never CLI arguments.
"""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
import zipfile

RFC = 'SAS2108197G0'
MAX_PACKAGE = 50 * 1024 * 1024
MAX_EXPANDED = 250 * 1024 * 1024


class SafeError(Exception):
    """Only sanitized codes may reach command output."""


def secret(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise SafeError('secret_file_required')
    mode = p.stat()
    if stat.S_IMODE(mode.st_mode) & 0o077 or mode.st_uid != os.geteuid():
        raise SafeError('secret_file_permissions')
    if mode.st_size > 1024 * 1024:
        raise SafeError('secret_file_too_large')
    return p.read_bytes()


def credentials(env=os.environ):
    """No network. No private data in output or exceptions."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    try:
        cert_data = secret(env['SAT_EFIRMA_CER_FILE'])
        key_data = secret(env['SAT_EFIRMA_KEY_FILE'])
        password = secret(env['SAT_EFIRMA_PASSWORD_FILE']).rstrip(b'\r\n')
        cert = x509.load_der_x509_certificate(cert_data)
        key = serialization.load_der_private_key(key_data, password=password)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise SafeError('rsa_key_required')
        fmt = dict(encoding=serialization.Encoding.DER, format=serialization.PublicFormat.SubjectPublicKeyInfo)
        if cert.public_key().public_bytes(**fmt) != key.public_key().public_bytes(**fmt):
            raise SafeError('certificate_key_mismatch')
        attrs = cert.subject.get_attributes_for_oid(x509.ObjectIdentifier('2.5.4.45'))
        rfc = attrs[0].value.split('/')[0].strip().upper() if attrs else ''
        if rfc != RFC or env.get('SAT_DOWNLOAD_RFC', RFC) != RFC:
            raise SafeError('rfc_outside_pilot')
        now = datetime.now(timezone.utc)
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise SafeError('certificate_not_current')
        return (cert_data, key_data, password), {
            'rfc': rfc, 'expires_at': cert.not_valid_after_utc.isoformat(),
            'certificate_sha256': hashlib.sha256(cert_data).hexdigest(),
            'key_matches': True, 'sat_authentication': 'not_attempted',
            'certificate_chain_and_revocation': 'not_checked',
        }
    except SafeError:
        raise
    except Exception:
        raise SafeError('credential_validation_failed') from None


def sat_client(env=os.environ):
    material, _ = credentials(env)
    try:
        import requests
        from lxml import etree
        from satcfdi.models import Signer
        from satcfdi.models.certificate import CertificateType
        from satcfdi.pacs.sat import SAT
        signer = Signer.load(certificate=material[0], key=material[1], password=material[2])
        # SDK classification is an additional check, not a certificate-chain proof.
        if signer.type != CertificateType.Fiel or str(signer.rfc) != RFC:
            raise SafeError('fiel_required')

        class BoundedSAT(SAT):
            def _request(self, soap_url, data, soap_action, needs_token_fn, verify=True):
                parsed = urlsplit(soap_url)
                hosts = {'cfdidescargamasivasolicitud.clouda.sat.gob.mx',
                         'cfdidescargamasiva.clouda.sat.gob.mx'}
                if (parsed.scheme != 'https' or parsed.hostname not in hosts
                        or parsed.port not in (None, 443) or parsed.username or parsed.password or not verify):
                    raise SafeError('sat_destination_denied')
                with requests.post(soap_url, data=data,
                        headers=self._get_headers(soap_action, needs_token_fn=needs_token_fn),
                        timeout=(10, 45), verify=True, allow_redirects=False, stream=True) as response:
                    if response.status_code != 200:
                        raise SafeError('sat_http_failed')
                    content = bytearray()
                    for chunk in response.iter_content(65536):
                        content.extend(chunk)
                        if len(content) > MAX_PACKAGE * 2:
                            raise SafeError('sat_response_too_large')
                parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
                return etree.fromstring(bytes(content), parser=parser)
        return BoundedSAT(signer=signer)
    except SafeError:
        raise
    except Exception:
        raise SafeError('sat_client_unavailable') from None


def open_store(directory):
    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise SafeError('private_staging_directory_required')
    db = sqlite3.connect(root / 'acquisition.sqlite')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('''CREATE TABLE IF NOT EXISTS jobs (
        day TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
        request_id TEXT, packages TEXT, failures INTEGER NOT NULL DEFAULT 0,
        next_at REAL NOT NULL DEFAULT 0, completed_at TEXT,
        PRIMARY KEY(day,kind))''')
    db.execute('''CREATE TABLE IF NOT EXISTS packages (
        package_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, filename TEXT NOT NULL,
        bytes INTEGER NOT NULL, acquired_at TEXT NOT NULL)''')
    db.commit()
    os.chmod(root / 'acquisition.sqlite', 0o600)
    return db


def plan(db, start, end):
    if start > end or end >= datetime.now(ZoneInfo('America/Mexico_City')).date() or (end-start).days > 366:
        raise SafeError('closed_day_range_required_max_367_days')
    day = start
    while day <= end:
        for kind in ('CFDI', 'Metadata'):
            db.execute('INSERT OR IGNORE INTO jobs(day,kind,state) VALUES(?,?,?)',
                       (day.isoformat(), kind, 'planned'))
        day += timedelta(days=1)
    db.commit()


def save_package(db, root, package_id, encoded):
    if not isinstance(package_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', package_id):
        raise SafeError('invalid_package_id')
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_PACKAGE:
            raise SafeError('package_too_large')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 20000 or sum(x.file_size for x in entries) > MAX_EXPANDED:
                raise SafeError('archive_limits_exceeded')
            if any(x.flag_bits & 1 for x in entries):
                raise SafeError('encrypted_archive_denied')
            # No extraction. Check ZIP integrity with bounded declared sizes.
            for entry in entries:
                if not entry.is_dir():
                    with archive.open(entry) as source:
                        count = 0
                        while chunk := source.read(65536):
                            count += len(chunk)
                            if count > entry.file_size or count > MAX_EXPANDED:
                                raise SafeError('archive_limits_exceeded')
    except SafeError:
        raise
    except Exception:
        raise SafeError('invalid_zip_package') from None
    digest = hashlib.sha256(data).hexdigest()
    prior = db.execute('SELECT sha256 FROM packages WHERE package_id=?', (package_id,)).fetchone()
    if prior and prior['sha256'] != digest:
        raise SafeError('package_content_conflict')
    name = digest + '.zip'
    target = Path(root) / name
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise SafeError('stored_package_corrupt')
    if not target.exists():
        fd, tmp = tempfile.mkstemp(dir=root)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
    db.execute('INSERT OR IGNORE INTO packages VALUES(?,?,?,?,?)',
        (package_id,digest,name,len(data),datetime.now(timezone.utc).isoformat()))
    db.commit()


def tick(db, root, client, at=None):
    at = time.time() if at is None else at
    # Called under the single-worker lock; surviving submissions need manual recovery.
    db.execute("UPDATE jobs SET state='submission_uncertain' WHERE state='submitting'")
    db.commit()
    row = db.execute("SELECT * FROM jobs WHERE state IN ('planned','waiting') AND next_at<=? ORDER BY day,kind LIMIT 1", (at,)).fetchone()
    if not row: return 'idle'
    identity = (row['day'], row['kind'])
    try:
        if row['state'] == 'planned':
            # An ambiguous submission is never retried automatically: no request ID to recover.
            db.execute("UPDATE jobs SET state='submitting' WHERE day=? AND kind=?", identity)
            db.commit()
            day = datetime.fromisoformat(row['day'])
            response = client.recover_comprobante_emitted_request(
                fecha_inicial=day, fecha_final=day+timedelta(days=1)-timedelta(seconds=1),
                rfc_emisor=RFC, tipo_solicitud=row['kind'],
                estado_comprobante='Vigente' if row['kind']=='CFDI' else 'Todos')
            if str(response.get('CodEstatus')) != '5000' or not response.get('IdSolicitud'):
                raise SafeError('sat_submission_not_accepted')
            db.execute("UPDATE jobs SET state='waiting',request_id=?,next_at=? WHERE day=? AND kind=?",
                (str(response['IdSolicitud']),at+900,*identity))
            db.commit()
            return 'submitted'
        response = client.recover_comprobante_status(row['request_id'])
        if str(response.get('CodEstatus')) != '5000':
            raise SafeError('sat_status_not_accepted')
        state = int(response.get('EstadoSolicitud', 0))
        if state in (1,2):
            db.execute('UPDATE jobs SET next_at=? WHERE day=? AND kind=?', (at+900,*identity))
            db.commit()
            return 'waiting'
        if state != 3:
            db.execute("UPDATE jobs SET state='blocked' WHERE day=? AND kind=?", identity)
            db.commit()
            return 'blocked'
        ids = response.get('IdsPaquetes') or []
        if not isinstance(ids, list) or len(ids)>10000:
            raise SafeError('invalid_package_list')
        count = int(response['NumeroCFDIs'])
        if count < 0: raise SafeError('invalid_document_count')
        if count and not ids:
            raise SafeError('incomplete_package_list')
        for package_id in ids:
            stored = db.execute('SELECT filename,sha256 FROM packages WHERE package_id=?',(package_id,)).fetchone()
            if stored:
                path = Path(root)/stored['filename']
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=stored['sha256']:
                    raise SafeError('stored_package_missing_or_corrupt')
                continue
            answer, encoded = client.recover_comprobante_download(id_paquete=package_id)
            if str(answer.get('CodEstatus')) != '5000':
                raise SafeError('sat_download_not_accepted')
            save_package(db, root, package_id, encoded)
        db.execute("UPDATE jobs SET state='acquired',packages=?,completed_at=? WHERE day=? AND kind=?",
            (json.dumps(ids),datetime.now(timezone.utc).isoformat(),*identity))
        db.commit()
        return 'acquired'
    except Exception:
        db.rollback()
        failures = row['failures']+1
        next_state = 'submission_uncertain' if row['state']=='planned' else 'waiting'
        db.execute('UPDATE jobs SET state=?,failures=?,next_at=? WHERE day=? AND kind=?',
            (next_state,failures,at+min(21600,900*2**min(failures-1,5)),*identity))
        db.commit()
        return next_state


@contextlib.contextmanager
def worker_lock(root):
    fd = os.open(Path(root)/'worker.lock', os.O_CREAT|os.O_RDWR, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise SafeError('worker_already_running') from None
        yield
    finally: os.close(fd)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preflight','plan','tick','status'])
    parser.add_argument('--from-date', type=date.fromisoformat)
    parser.add_argument('--to-date', type=date.fromisoformat)
    args=parser.parse_args()
    try:
        if args.command=='preflight':
            print(json.dumps(credentials()[1])); return
        root=os.environ.get('SAT_DOWNLOAD_STAGING_DIR')
        if not root: raise SafeError('persistent_private_staging_directory_required')
        os.umask(0o077)
        with worker_lock_after_mkdir(root):
            with contextlib.closing(open_store(root)) as db:
                if args.command=='plan':
                    if not args.from_date or not args.to_date: raise SafeError('date_range_required')
                    plan(db,args.from_date,args.to_date)
                elif args.command=='tick':
                    if os.environ.get('SAT_DOWNLOAD_ENABLED')!='1': raise SafeError('download_disabled')
                    print(json.dumps({'outcome':tick(db,root,sat_client())})); return
                print(json.dumps({'jobs':dict(db.execute('SELECT state,count(*) FROM jobs GROUP BY state').fetchall()),
                                  'stage':'acquisition_only_not_ingested_into_cima'}))
    except Exception as error:
        print(json.dumps({'error':str(error) if isinstance(error,SafeError) else 'operation_failed'}))
        raise SystemExit(1) from None


@contextlib.contextmanager
def worker_lock_after_mkdir(root):
    path=Path(root)
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode)&0o077:
        raise SafeError('private_staging_directory_required')
    with worker_lock(path): yield


if __name__=='__main__': main()
