"""Opt-in REP replacement on a COPY of the completed public sandbox recovery.
One new REP, one retrieval, one cancellation request, one status query maximum.
Never deploy this script or pass a customer/production database.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlparse
from uuid import uuid4
from xml.etree import ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-public-sandbox', action='store_true')
    parser.add_argument('--source-evidence', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute_public_sandbox:
        parser.error('Explicit public sandbox opt-in required')
    source, folder = args.source_evidence.resolve(), args.evidence_dir.resolve()
    initial = json.loads((source/'report.json').read_text())
    if (initial.get('mode') != 'real-public-pac-sandbox' or initial.get('result') != 'passed'
        or initial.get('production_changed') is not False or not (source/'pilot.sqlite').is_file()):
        parser.error('Completed disposable public REP recovery required')
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(source/'pilot.sqlite', folder/'pilot.sqlite')
    source_db_hash = hashlib.sha256((source/'pilot.sqlite').read_bytes()).hexdigest()
    os.environ.update(DATABASE_URL='sqlite:///:memory:', DB_URL='sqlite:///:memory:',
        AUTH_CONNECTOR='jwt', AUTH_ALLOW_ANONYMOUS='false', TIMBRACFDI_ENV='sandbox', TIMBRACFDI_USE_SOAP='false',
        TIMBRACFDI_SANDBOX_BASE_URL='https://pruebas.timbracfdi33.mx:1444/api/v2',
        TIMBRACFDI_SANDBOX_TOKEN='mvpNUXmQfK8=', TIMBRACFDI_TOKEN='', TIMBRACFDI_PRODUCTION_TOKEN='',
        FISCAL_PPD_ENABLED='true', FISCAL_REP_STAMP_ENABLED='true',
        FISCAL_CORRECTIONS_ENABLED='true', FISCAL_CANCELLATION_ENABLED='true',
        FISCAL_PPD_PRODUCTION_ENABLED='false', FISCAL_CORRECTIONS_PRODUCTION_ENABLED='false',
        FISCAL_CANCELLATION_PRODUCTION_ENABLED='false')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import requests
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.auth import SecurityContext
    from app.api.routers import payments_v1 as payments, fiscal_revisions_v1 as revisions
    from app.models.billing_payment import BillingPayment, PaymentReceipt
    from app.models.commercial import BillingDraft
    from app.models.fiscal_revision import FiscalRevision
    from app.models import csf, evento, usuario, sucursal
    from app.services import timbracfdi_client as pac, fiscal_revisions as fiscal, payment_receipts

    report = dict(result='incomplete', mode='public-sandbox-REP-substitution', production_changed=False,
        database='copy of disposable SQLite', identity='synthetic local operator', browser_tested=False,
        signature_verified=False, source_database_sha256=source_db_hash, source_receipt_uuid=initial['receipt_uuid'],
        draft_id=initial['draft_id'], payment_id=initial['payment_id'], requests=[], steps=[], local_http=[])
    def save(name, value):
        (folder/name).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    def check(name, condition):
        report['steps'].append(dict(name=name, passed=bool(condition))); save('report.json', report)
        if not condition:
            raise AssertionError(name)
    ctx = SecurityContext('public-rep-recovery-qa', 'local-pilot', 'operator')
    engine = create_engine('sqlite:///'+str(folder/'pilot.sqlite'), connect_args={'check_same_thread':False})
    sessions = sessionmaker(bind=engine)
    captured = {}
    original_http, original_stamp = requests.sessions.Session.request, pac.timbra_cfdi
    expected = ['TimbraCFDI','ObtieneCFDI','CancelaCFDIAck','ConsultaEstatusSat']
    def guard(session, method, url, **kwargs):
        p = urlparse(url); index = len(report['requests']); body = kwargs.get('json', {})
        if (index >= 4 or method.upper() != 'POST' or p.scheme != 'https' or p.hostname != 'pruebas.timbracfdi33.mx'
            or p.port != 1444 or p.username or p.password or p.query or p.fragment
            or p.path != '/api/v2/Timbrado/'+expected[index]):
            raise RuntimeError('Unexpected destination or repeated PAC operation blocked')
        if index == 1 and body.get('FolioFiscal') != captured.get('uuid'):
            raise RuntimeError('Wrong retrieval UUID blocked')
        if index == 2 and (body.get('RfcEmisor') != 'IIA040805DZ4' or body.get('FolioFiscal') != initial['receipt_uuid']
            or body.get('MotivoCancelacion') != '01' or body.get('FolioFiscalSustitucion') != captured.get('uuid')):
            raise RuntimeError('Wrong cancellation scope blocked')
        if index == 3 and body.get('FolioFiscal') != initial['receipt_uuid']:
            raise RuntimeError('Wrong status UUID blocked')
        entry = dict(operation=expected[index], outcome='unknown'); report['requests'].append(entry)
        save(f'{index+1:02}-request.json', body); save('report.json', report)
        kwargs.update(timeout=(10, 40), allow_redirects=False)
        try:
            response = original_http(session, method, url, **kwargs)
        except Exception as exc:
            entry['transport_exception_type'] = type(exc).__name__
            save('report.json', report)
            raise
        entry.update(http_status=response.status_code, outcome='response')
        try: value = response.json()
        except ValueError: value = dict(body_sha256=hashlib.sha256(response.content).hexdigest())
        save(f'{index+1:02}-response.json', value); save('report.json', report)
        return response
    def lose_acceptance(**kwargs):
        result = original_stamp(**kwargs); info = pac.extract_stamped_document(result)
        if not info['success']:
            return result
        captured.update(uuid=pac.extract_stamp_uuid(info['stamped_xml']).lower(), xml=info['stamped_xml'])
        (folder/'replacement-accepted.xml').write_text(captured['xml'])
        captured['fault_injected'] = True
        raise TimeoutError('Deliberate replacement response loss before Digestor confirmation')
    requests.sessions.Session.request = guard
    app = FastAPI(); app.include_router(revisions.router); app.include_router(payments.router)
    def database():
        with sessions() as db: yield db
    app.dependency_overrides[revisions.get_db] = database
    app.dependency_overrides[revisions.read_roles] = lambda: ctx
    app.dependency_overrides[revisions.write_roles] = lambda: ctx
    try:
        with sessions() as db:
            draft = db.get(BillingDraft, initial['draft_id']); payment = db.get(BillingPayment, initial['payment_id'])
            receipt = db.query(PaymentReceipt).filter_by(payment_id=payment.id).one()
            check('source_is_exact_public_test_invoice_and_receipt', draft.company_id == ctx.tenant_id
                and draft.emitter_rfc == 'IIA040805DZ4' and draft.customer_rfc == 'EKU9003173C9'
                and receipt.uuid == initial['receipt_uuid'] and db.query(BillingPayment).count() == 1
                and db.query(FiscalRevision).count() == 0)
            source_xml, declaration = receipt.xml_base64, payment.declaration
            (folder/'source-receipt.xml').write_bytes(base64.b64decode(source_xml))
        with TestClient(app) as client:
            payments_path = '/v1/billing/drafts/'+initial['draft_id']+'/payments'
            path = payments_path+'/'+initial['payment_id']+'/receipt/correction'
            def call(operation='', method='GET', body=None):
                url = path+('/'+operation if operation else '')
                response = client.request(method, url, json=body) if body is not None else client.request(method, url)
                report['local_http'].append(dict(operation=operation or 'root', method=method, status=response.status_code))
                save('report.json', report)
                return response
            before = client.get(payments_path).json(); save('balance-before.json', before)
            payload = dict(source_uuid=initial['receipt_uuid'], idempotency_key=str(uuid4()),
                reason='Ensayo QA de corrección de referencia y forma sin cambio económico',
                changes=dict(payment_form='02', operation_reference='LAB-SUSTITUCION-20261109'))
            prepared = call(method='POST', body=payload)
            check('replacement_prepared', prepared.status_code == 200 and prepared.json()['pending'])
            revision_id = prepared.json()['history'][-1]['id']; report['revision_id'] = revision_id
            check('create_is_idempotent', call(method='POST', body=payload).json()['history'][-1]['id'] == revision_id)
            check('cannot_cancel_before_replacement_confirmed', call('cancel-original','POST').status_code == 409)
            pac.timbra_cfdi = lose_acceptance
            uncertain = call('stamp','POST'); save('replacement-unknown.json', uncertain.json())
            check('accepted_once_then_unknown', uncertain.status_code == 200 and captured.get('fault_injected')
                and uncertain.json()['history'][-1]['status'] == 'unknown' and uncertain.json()['active_uuid'] == initial['receipt_uuid'])
            check('reissue_and_abandon_blocked', call('stamp','POST').status_code == 409
                and call('abandon','POST').status_code == 409 and len(report['requests']) == 1)
            pac.timbra_cfdi = original_stamp
            recovered = call('reconcile','POST',dict(receipt_uuid=captured['uuid'])); save('replacement-recovered.json',recovered.json())
            check('same_replacement_recovered_without_stamp', recovered.status_code == 200
                and recovered.json()['history'][-1]['status'] == 'stamped'
                and recovered.json()['history'][-1]['result_uuid'] == captured['uuid']
                and recovered.json()['active_uuid'] == initial['receipt_uuid'] and recovered.json()['pending'])
            replacement = base64.b64decode(recovered.json()['history'][-1]['result_xml_base64']).decode()
            (folder/'replacement-recovered.xml').write_text(replacement)
            check('complete_retrieved_XML_identity', ET.canonicalize(replacement) == ET.canonicalize(captured['xml']))
            root = fiscal.parse(replacement); related = root.find('c:CfdiRelacionados/c:CfdiRelacionado',fiscal.NS)
            pago = root.find('c:Complemento/p:Pagos/p:Pago',fiscal.NS)
            check('relation_and_only_requested_data_changed', related.get('UUID') == initial['receipt_uuid']
                and pago.get('FormaDePagoP') == '02' and pago.get('NumOperacion') == 'LAB-SUSTITUCION-20261109'
                and pago.get('Monto') == '116.00')
            requested = call('cancel-original','POST'); save('cancellation-request.json', requested.json())
            checked = call('confirm-cancellation','POST'); save('cancellation-checked.json', checked.json())
            count = len(report['requests'])
            repeated = call('cancel-original','POST')
            check('cancellation_is_not_resent', repeated.status_code == 200 and len(report['requests']) == count)
            after = client.get(payments_path).json(); save('balance-after.json', after)
            current = call().json(); save('active-document.json',current)
            check('one_economic_payment_and_zero_balance_preserved', len(after['payments']) == 1
                and after['payments'][0]['id'] == initial['payment_id'] and after['paid'] == before['paid'] == '116.00'
                and after['balance'] == before['balance'] == '0.00')
            cancelled = current['history'][-1]['source_cancellation_status'] == 'cancelled'
            check('active_document_changes_only_after_confirmed_cancellation',
                (cancelled and current['active_uuid'] == captured['uuid'] and not current['pending'])
                or (not cancelled and current['active_uuid'] == initial['receipt_uuid'] and current['pending']))
            report.update(result='passed' if cancelled else 'partial_cancellation_unconfirmed', replacement_uuid=captured['uuid'],
                source_cancellation_status=current['history'][-1]['source_cancellation_status'],
                active_uuid=current['active_uuid'], pending=current['pending'], fiscal_complete=after['fiscal_complete'])
        with sessions() as db:
            receipt = db.query(PaymentReceipt).filter_by(payment_id=initial['payment_id']).one()
            payment = db.get(BillingPayment, initial['payment_id']); row = db.get(FiscalRevision,revision_id)
            check('source_XML_and_payment_declaration_untouched', receipt.xml_base64 == source_xml and payment.declaration == declaration)
            fiscal.verify(db,row); payment_receipts.verify_acts(db,payment)
        check('source_database_unchanged', hashlib.sha256((source/'pilot.sqlite').read_bytes()).hexdigest() == source_db_hash)
    except Exception as exc:
        report.update(result='stopped',error=dict(type=type(exc).__name__,detail=str(exc)))
    finally:
        pac.timbra_cfdi = original_stamp; requests.sessions.Session.request = original_http
        save('report.json',report); engine.dispose(); print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 2 if report['result'] == 'partial_cancellation_unconfirmed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
