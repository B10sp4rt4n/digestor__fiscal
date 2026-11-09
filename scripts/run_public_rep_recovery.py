"""Opt-in PAC sandbox REP response-loss recovery, with disposable local SQL.

One PPD invoice, one REP acceptance, one ObtieneCFDI. Never deploy this harness.
"""
import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from xml.etree import ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-public-sandbox', action='store_true')
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--resume-confirmed', action='store_true', help='Finish only a persisted recovery checkpoint; no PAC calls allowed')
    args = parser.parse_args()
    if not args.execute_public_sandbox:
        parser.error('Explicit sandbox opt-in required')
    folder = args.evidence_dir.resolve()
    if args.resume_confirmed:
        if not (folder/'pilot.sqlite').is_file() or not (folder/'recovered.json').is_file():
            parser.error('Confirmed recovery checkpoint required')
    else:
        folder.mkdir(parents=True, exist_ok=False)
    os.environ.update(DATABASE_URL='sqlite:///:memory:', DB_URL='sqlite:///:memory:',
        AUTH_CONNECTOR='jwt', AUTH_ALLOW_ANONYMOUS='false', FISCAL_PPD_ENABLED='true',
        FISCAL_REP_STAMP_ENABLED='true', FISCAL_PPD_PRODUCTION_ENABLED='false',
        TIMBRACFDI_ENV='sandbox', TIMBRACFDI_USE_SOAP='false',
        TIMBRACFDI_SANDBOX_BASE_URL='https://pruebas.timbracfdi33.mx:1444/api/v2',
        TIMBRACFDI_SANDBOX_TOKEN='mvpNUXmQfK8=', TIMBRACFDI_TOKEN='', TIMBRACFDI_PRODUCTION_TOKEN='')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import requests
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.auth import SecurityContext
    from app.db.base import Base
    from app.api.routers import payments_v1 as api, commercial_v1 as commercial
    from app.models.commercial import ProductCatalog, BillingDraft, BillingDraftItem
    from app.models.billing_payment import BillingPayment, PaymentReceipt, PaymentAct
    from app.models.fiscal_act import FiscalAct
    from app.models.fiscal_revision import FiscalRevision, FiscalRevisionAct
    from app.models.billing_cancellation import BillingCancellation, CancellationAct
    from app.models.evento import EventoFacturacion
    from app.models import csf, usuario, sucursal  # register metadata dependencies
    from app.services import timbracfdi_client as pac, payment_receipts as service

    report = dict(result='incomplete', mode='real-public-pac-sandbox', local_sql='disposable SQLite',
        digestor_http='in-process ASGI TestClient', identity='synthetic local operator',
        production_changed=False, customer_data_used=False, signature_verified=False,
        requests=[], steps=[], local_http=[])
    captured = {}
    def save(name, data):
        (folder/name).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    def check(name, condition):
        report['steps'].append(dict(name=name, passed=bool(condition)))
        save('report.json', report)
        if not condition:
            raise AssertionError(name)
    def same_document(a, b):
        return ET.canonicalize(a) == ET.canonicalize(b)

    if args.resume_confirmed:
        report = json.loads((folder/'report.json').read_text())
        if (report.get('result') != 'stopped' or report.get('error', {}).get('detail') != 'ObtieneCFDI_recovers_exact_REP'
            or [row['operation'] for row in report['requests']] != ['TimbraCFDI','TimbraCFDI','ObtieneCFDI']
            or any(row.get('http_status') != 200 for row in report['requests'])):
            parser.error('Checkpoint is not the known completed retrieval')
        save('initial-report.json', report)
        report['steps'] = [row for row in report['steps'] if row['passed']]
        report.pop('error', None)
        report.update(resumed_without_PAC_calls=True, result='incomplete',
            harness_correction='Compare canonical full XML; PAC reordered only an xmlns attribute. Initial failed byte check is retained separately.')

    original_http = requests.sessions.Session.request
    expected = ['TimbraCFDI', 'TimbraCFDI', 'ObtieneCFDI']
    def guard(session, method, url, **kwargs):
        parsed = urlparse(url)
        index = len(report['requests'])
        if (index >= len(expected) or method.upper() != 'POST' or parsed.scheme != 'https'
            or parsed.hostname != 'pruebas.timbracfdi33.mx' or parsed.port != 1444
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != '/api/v2/Timbrado/'+expected[index]):
            raise RuntimeError('Additional issuance or non-sandbox request blocked')
        payload = kwargs.get('json', {})
        entry = dict(operation=expected[index], outcome='unknown')
        report['requests'].append(entry)
        save(f'{index+1:02}-request.json', payload)
        save('report.json', report)
        kwargs.update(timeout=(10, 40), allow_redirects=False)
        response = original_http(session, method, url, **kwargs)
        entry.update(http_status=response.status_code, outcome='response')
        save(f'{index+1:02}-response.json', response.json())
        save('report.json', report)
        return response
    requests.sessions.Session.request = guard
    original_stamp = pac.timbra_cfdi
    def lose_rep_response(**kwargs):
        result = original_stamp(**kwargs)
        info = pac.extract_stamped_document(result)
        if not info['success']:
            return result
        captured.update(uuid=pac.extract_stamp_uuid(info['stamped_xml']), xml=info['stamped_xml'])
        check('PAC_accepted_REP', bool(captured['uuid']))
        (folder/'rep-accepted.xml').write_text(captured['xml'])
        captured['fault_injected'] = True
        raise TimeoutError('Deliberate PAC response loss BEFORE Digestor persists acceptance')

    engine = create_engine('sqlite:///'+str(folder/'pilot.sqlite'), connect_args={'check_same_thread':False})
    tables = [ProductCatalog, BillingDraft, BillingDraftItem, FiscalAct, BillingPayment, PaymentReceipt,
        PaymentAct, BillingCancellation, CancellationAct, FiscalRevision, FiscalRevisionAct, EventoFacturacion]
    Base.metadata.create_all(engine, tables=[model.__table__ for model in tables])
    sessions = sessionmaker(bind=engine)
    ctx = SecurityContext('public-rep-recovery-qa', 'local-pilot', 'operator')
    app = FastAPI()
    app.include_router(api.router)
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[api.get_db] = database
    app.dependency_overrides[api.read_roles] = lambda: ctx
    app.dependency_overrides[api.write_roles] = lambda: ctx
    if args.resume_confirmed:
        try:
            accepted_xml = (folder/'rep-accepted.xml').read_text()
            recovered = json.loads((folder/'recovered.json').read_text())
            recovered_xml = base64.b64decode(recovered['xml_base64']).decode()
            receipt_uuid = pac.extract_stamp_uuid(accepted_xml)
            check('full_XML_canonical_identity_and_same_UUID', same_document(accepted_xml, recovered_xml)
                and recovered['receipt_uuid'] == receipt_uuid)
            before = json.loads((folder/'balance-unknown.json').read_text())
            path = '/v1/billing/drafts/'+report['draft_id']+'/payments'
            suffix = '/'+report['payment_id']+'/receipt'
            with TestClient(app) as client:
                for _ in range(2):
                    response = client.post(path+suffix+'/reconcile', json=dict(receipt_uuid=receipt_uuid))
                    check('idempotent_recovery_without_PAC', response.status_code == 200
                        and response.json()['xml_base64'] == recovered['xml_base64'])
                check('foreign_UUID_rejected_without_PAC', client.post(path+suffix+'/reconcile',
                    json=dict(receipt_uuid=report['invoice_uuid'])).status_code == 409)
                after = client.get(path).json(); save('balance-recovered.json', after)
                check('payment_and_balance_preserved', len(after['payments']) == 1
                    and after['payments'][0]['id'] == report['payment_id'] and after['balance'] == before['balance'] == '0.00'
                    and after['paid'] == before['paid'] == '116.00' and after['fiscal_complete'] is True)
            with sessions() as db:
                payment = db.get(BillingPayment, report['payment_id'])
                receipt = db.query(PaymentReceipt).filter_by(payment_id=payment.id).one()
                service.verify_acts(db, payment)
                actions = [json.loads(row.payload)['action_or_state'] for row in db.query(PaymentAct).filter_by(payment_id=payment.id).order_by(PaymentAct.sequence)]
                check('local_chain_valid_unknown_then_recovered', actions == ['payment.registered','payment.reserved','payment.prepared','payment.unknown','payment.recovered']
                    and receipt.status == 'stamped' and receipt.xml_base64 == recovered['xml_base64'] and db.query(BillingPayment).count() == 1)
            check('exactly_three_initial_PAC_requests', len(report['requests']) == 3)
            report.update(result='passed', receipt_uuid=receipt_uuid, accepted_REPs=1, actions=actions,
                XML_byte_identical=accepted_xml == recovered_xml, XML_canonical_identical=True,
                accepted_xml_sha256=hashlib.sha256(accepted_xml.encode()).hexdigest(),
                recovered_xml_sha256=hashlib.sha256(recovered_xml.encode()).hexdigest())
        except Exception as exc:
            report.update(result='stopped', error=dict(type=type(exc).__name__, detail=str(exc)))
        finally:
            requests.sessions.Session.request = original_http
            save('report.json', report); engine.dispose()
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['result'] == 'passed' else 1
    try:
        with sessions() as db:
            draft = BillingDraft(id=str(uuid4()), company_id=ctx.tenant_id, status='ready_to_stamp',
                emitter_rfc='IIA040805DZ4', emitter_name='INDISTRIA ILUMINADORA DE ALMACENES', emitter_regimen='626', place_of_issue='32690',
                customer_rfc='EKU9003173C9', customer_name='ESCUELA KEMPER URGATE', customer_zip='42501', customer_regimen='603',
                customer_use_cfdi='G03', currency='MXN', payment_method='PPD', payment_form='99',
                subtotal=100, taxes=16, total=116, items=[BillingDraftItem(company_id=ctx.tenant_id,
                    description='REP RECOVERY QA SIN VALIDEZ FISCAL', quantity=1, unit_price=100,
                    sat_product_code='80101500', unit_code='E48', line_subtotal=100, line_taxes=16, line_total=116)])
            db.add(draft); db.commit()
            commercial.stamp_billing_draft(draft.id, commercial.BillingDraftStampRequest(), None, ctx, db)
            check('PPD_invoice_issued_once', draft.stamp_status == 'stamped')
            report.update(draft_id=draft.id, invoice_uuid=pac.extract_stamp_uuid(base64.b64decode(draft.stamped_xml_base64).decode()))
            (folder/'invoice.xml').write_bytes(base64.b64decode(draft.stamped_xml_base64))
        with TestClient(app) as client:
            path = '/v1/billing/drafts/'+report['draft_id']+'/payments'
            def call(suffix='', method='GET', body=None):
                response = client.request(method, path+suffix, json=body) if body is not None else client.request(method, path+suffix)
                report['local_http'].append(dict(method=method, suffix=suffix, status=response.status_code))
                save('report.json', report)
                return response
            payload = dict(amount='116.00', reference='PUBLIC-REP-'+str(uuid4()), payment_form='03',
                paid_at=datetime.now(timezone.utc).isoformat(), idempotency_key=str(uuid4()))
            registered = call(method='POST', body=payload)
            check('payment_registered', registered.status_code == 201)
            payment_id = registered.json()['id']; report['payment_id'] = payment_id
            check('registration_idempotent', call(method='POST', body=payload).json()['id'] == payment_id)
            suffix = '/'+payment_id+'/receipt'
            pac.timbra_cfdi = lose_rep_response
            stamped = call(suffix+'/stamp', 'POST')
            check('lost_acceptance_persists_unknown', stamped.status_code == 502 and captured.get('fault_injected')
                and call(suffix).json()['receipt_status'] == 'unknown')
            before = call().json(); save('balance-unknown.json', before)
            check('one_payment_zero_balance_fiscal_pending', len(before['payments']) == 1 and before['balance'] == '0.00'
                and before['economic_settled'] is True and before['fiscal_complete'] is False)
            retry = call(suffix+'/stamp', 'POST')
            check('retry_blocked_before_PAC', retry.status_code == 409 and len(report['requests']) == 2)
            pac.timbra_cfdi = original_stamp
            recovered = call(suffix+'/reconcile', 'POST', dict(receipt_uuid=captured['uuid']))
            save('recovered.json', recovered.json())
            check('ObtieneCFDI_recovers_exact_REP', recovered.status_code == 200 and
                recovered.json()['receipt_uuid'] == captured['uuid'] and
                same_document(base64.b64decode(recovered.json()['xml_base64']).decode(), captured['xml']))
            (folder/'rep-recovered.xml').write_bytes(base64.b64decode(recovered.json()['xml_base64']))
            repeated = call(suffix+'/reconcile', 'POST', dict(receipt_uuid=captured['uuid']))
            check('repeat_recovery_has_no_PAC_call', repeated.status_code == 200 and len(report['requests']) == 3)
            check('foreign_UUID_rejected_without_PAC', call(suffix+'/reconcile', 'POST', dict(receipt_uuid=report['invoice_uuid'])).status_code == 409
                and len(report['requests']) == 3)
            after = call().json(); save('balance-recovered.json', after)
            check('payment_and_balance_preserved', len(after['payments']) == 1 and after['payments'][0]['id'] == payment_id
                and after['paid'] == before['paid'] == '116.00' and after['balance'] == before['balance'] == '0.00'
                and after['fiscal_complete'] is True)
        with sessions() as db:
            payment = db.get(BillingPayment, payment_id)
            receipt = db.query(PaymentReceipt).filter_by(payment_id=payment_id).one()
            service.verify_acts(db, payment)
            actions = [json.loads(row.payload)['action_or_state'] for row in db.query(PaymentAct).filter_by(payment_id=payment_id).order_by(PaymentAct.sequence)]
            check('local_chain_valid_unknown_then_recovered', actions == ['payment.registered','payment.reserved','payment.prepared','payment.unknown','payment.recovered']
                and receipt.status == 'stamped' and db.query(BillingPayment).count() == 1)
            report.update(actions=actions, receipt_uuid=captured['uuid'], accepted_REPs=1,
                xml_sha256=hashlib.sha256(captured['xml'].encode()).hexdigest(), result='passed')
    except Exception as exc:
        report.update(result='stopped', error=dict(type=type(exc).__name__, detail=str(exc)))
    finally:
        pac.timbra_cfdi = original_stamp
        requests.sessions.Session.request = original_http
        save('report.json', report)
        engine.dispose()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
