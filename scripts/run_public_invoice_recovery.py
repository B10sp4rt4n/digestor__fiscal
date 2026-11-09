"""Opt-in public PAC QA: one synthetic invoice and one recovery; no production.

Public test access: https://developers.timbracfdi33.mx/docs/pase-a-producci%C3%B3n
Never retries an uncertain issuance or opens an existing evidence directory.
"""
import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-public-sandbox', action='store_true')
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute_public_sandbox: parser.error('Explicit public sandbox opt-in required')
    folder = args.evidence_dir.resolve(); folder.mkdir(parents=True, exist_ok=False)
    os.environ.update(DATABASE_URL='sqlite:///:memory:', DB_URL='sqlite:///:memory:', AUTH_CONNECTOR='jwt', AUTH_ALLOW_ANONYMOUS='false',
        TIMBRACFDI_ENV='sandbox', TIMBRACFDI_USE_SOAP='false',
        TIMBRACFDI_SANDBOX_BASE_URL='https://pruebas.timbracfdi33.mx:1444/api/v2',
        TIMBRACFDI_SANDBOX_TOKEN='mvpNUXmQfK8=', TIMBRACFDI_TOKEN='', TIMBRACFDI_PRODUCTION_TOKEN='')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import requests
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.core.auth import SecurityContext
    from app.db.base import Base
    from app.api.routers import commercial_v1 as api
    from app.models.commercial import BillingDraft, BillingDraftItem, ProductCatalog
    from app.models.evento import EventoFacturacion
    from app.models.fiscal_act import FiscalAct
    from app.models import csf, usuario, sucursal
    from app.services import invoice_recovery, timbracfdi_client as pac, fiscal_acts
    report = {'mode': 'public-vendor-sandbox', 'production_changed': False, 'customer_data_used': False,
        'signature_verified': False, 'requests': [], 'steps': [], 'result': 'incomplete'}
    def save(name, value): (folder/name).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    def check(name, passed):
        report['steps'].append({'name': name, 'passed': bool(passed)}); save('report.json', report)
        if not passed: raise RuntimeError(name)
    original_request = requests.sessions.Session.request
    def guarded(session, method, url, **kwargs):
        parsed = urlparse(url)
        if (method.upper() != 'POST' or parsed.scheme != 'https' or parsed.hostname != 'pruebas.timbracfdi33.mx'
            or parsed.port != 1444 or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('/api/v2/Timbrado/TimbraCFDI', '/api/v2/Timbrado/ObtieneCFDI')
            or len(report['requests']) >= 2): raise RuntimeError('Non-QA or additional request blocked')
        entry = {'operation': parsed.path.rsplit('/', 1)[-1], 'outcome': 'unknown'}
        report['requests'].append(entry); index = len(report['requests'])
        save(f'{index:02}-request.json', kwargs.get('json')); save('report.json', report)
        kwargs.update(timeout=(10, 30), allow_redirects=False)
        response = original_request(session, method, url, **kwargs)
        entry.update(http_status=response.status_code, outcome='response', response_sha256=hashlib.sha256(response.content).hexdigest())
        try: value = response.json()
        except ValueError: value = {'unparsed_body_sha256': entry['response_sha256']}
        save(f'{index:02}-response.json', value); save('report.json', report)
        return response
    requests.sessions.Session.request = guarded
    engine = create_engine('sqlite:///'+str(folder/'pilot.sqlite'))
    Base.metadata.create_all(engine, tables=[model.__table__ for model in (ProductCatalog, BillingDraft, BillingDraftItem, EventoFacturacion, FiscalAct)])
    ctx = SecurityContext('public-invoice-recovery-qa', 'local-pilot', 'operator')
    original_sender, captured = pac.timbra_cfdi, {}
    def lose_response(**kwargs):
        response = original_sender(**kwargs)
        info = pac.extract_stamped_document(response)
        if not info['success']: return response
        captured['uuid'] = pac.extract_stamp_uuid(info['stamped_xml'])
        check('provider_accepted_demo_invoice', bool(captured['uuid']))
        raise TimeoutError('Intentional local response loss after sandbox acceptance')
    pac.timbra_cfdi = lose_response
    try:
        with Session(engine) as db:
            draft = BillingDraft(id=str(uuid4()), company_id=ctx.tenant_id, status='ready_to_stamp',
                emitter_rfc='IIA040805DZ4', emitter_name='INDISTRIA ILUMINADORA DE ALMACENES', emitter_regimen='626', place_of_issue='32690',
                customer_rfc='EKU9003173C9', customer_name='ESCUELA KEMPER URGATE', customer_zip='42501', customer_regimen='603',
                customer_use_cfdi='G03', currency='MXN', payment_method='PUE', payment_form='03',
                subtotal=100, taxes=16, total=116, items=[BillingDraftItem(company_id=ctx.tenant_id,
                    description='PRUEBA QA COTIZA SIN VALIDEZ FISCAL', quantity=1, unit_price=100, sat_product_code='80101500', unit_code='E48',
                    line_subtotal=100, line_taxes=16, line_total=116)])
            db.add(draft); db.commit()
            try: api.stamp_billing_draft(draft.id, api.BillingDraftStampRequest(), None, ctx, db)
            except HTTPException as exc:
                if exc.status_code != 502: raise
            check('lost_response_persists_unknown', draft.stamp_status == 'unknown' and bool(captured.get('uuid')))
            invoice_recovery.recover(db, ctx, draft.id, captured['uuid'].lower())
            result = api.get_billing_draft_stamp_result(draft.id, None, ctx, db)
            check('provider_retrieval_matches_original_request', result['stamp_status'] == 'stamped' and result['uuid'].lower() == captured['uuid'].lower())
            saved_xml = result['xml_base64']
            invoice_recovery.recover(db, ctx, draft.id, captured['uuid'].lower())
            check('one_issue_one_query_one_metric', [row['operation'] for row in report['requests']] == ['TimbraCFDI','ObtieneCFDI'] and db.query(EventoFacturacion).count() == 1)
            check('idempotent_xml_and_valid_local_chain', draft.stamped_xml_base64 == saved_xml)
            fiscal_acts.verify(db.query(FiscalAct).order_by(FiscalAct.sequence).all())
            report.update(result='passed', invoice_uuid=result['uuid'], xml_sha256=hashlib.sha256(base64.b64decode(saved_xml)).hexdigest())
    except Exception as exc:
        report.update(result='stopped', error={'type':type(exc).__name__, 'detail':str(exc)})
    finally:
        save('report.json', report); engine.dispose()
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__': raise SystemExit(main())
