"""Opt-in live QA: public vendor demo identity/token, local SQLite, no customer data.

Never resumes a previous run or retries an uncertain issuance. The evidence directory
must not exist. No application startup, remote DB, registration or production config.
Public QA access: https://developers.timbracfdi33.mx/docs/pase-a-producci%C3%B3n
"""
import argparse
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.parse import urlparse
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-public-sandbox', action='store_true')
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--scenario', choices=('payments', 'payments-recovery', 'substitutions'), default='payments')
    args = parser.parse_args()
    if not args.execute_public_sandbox:
        parser.error('Explicit --execute-public-sandbox required; nothing was sent')
    folder = args.evidence_dir.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    os.environ.update(DATABASE_URL='sqlite:///:memory:', DB_URL='sqlite:///:memory:',
        TIMBRACFDI_ENV='sandbox', TIMBRACFDI_USE_SOAP='false',
        TIMBRACFDI_SANDBOX_BASE_URL='https://pruebas.timbracfdi33.mx:1444/api/v2',
        TIMBRACFDI_SANDBOX_TOKEN='mvpNUXmQfK8=', TIMBRACFDI_TOKEN='', TIMBRACFDI_PRODUCTION_TOKEN='',
        FISCAL_PPD_ENABLED='true', FISCAL_REP_STAMP_ENABLED='true',
        FISCAL_CANCELLATION_ENABLED='true', FISCAL_CORRECTIONS_ENABLED='true')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import requests
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.db.base import Base
    from app.core.auth import SecurityContext
    from app.api.routers import commercial_v1 as commercial, payments_v1 as payments
    from app.api.routers import fiscal_revisions_v1 as revisions, cancellations_v1 as cancellations
    from app.models.commercial import BillingDraft, BillingDraftItem, ProductCatalog
    from app.models.billing_payment import BillingPayment, PaymentReceipt, PaymentAct
    from app.models.billing_cancellation import BillingCancellation, CancellationAct
    from app.models.fiscal_revision import FiscalRevision, FiscalRevisionAct
    from app.models.fiscal_act import FiscalAct
    # Register the legacy mapper relationships without starting the application.
    from app.models import csf, evento, user, usuario, sucursal  # noqa: F401
    from app.schemas.commercial import DraftItemInput
    from app.schemas.billing_payment import RegisterPayment
    from app.schemas.billing_cancellation import CancelDocument, RecoverReceipt
    from fastapi import HTTPException
    from app.schemas.fiscal_revision import CreateFiscalRevision
    from app.services import timbracfdi_client as pac, fiscal_revisions as fiscal

    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'mode': 'live-public-vendor-QA',
        'host': 'pruebas.timbracfdi33.mx:1444', 'customer_data_used': False,
        'production_changed': False, 'signature_verified': False, 'scenario': args.scenario, 'steps': [], 'requests': []}

    def save(name, value):
        (folder / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))

    original_request = requests.sessions.Session.request

    def guarded_request(session, method, url, **kwargs):
        target = urlparse(url)
        if (method.upper() != 'POST' or target.scheme != 'https'
                or target.hostname != 'pruebas.timbracfdi33.mx' or target.port != 1444
                or target.username or target.password or target.query
                or target.path not in {'/api/v2/Timbrado/' + op for op in
                    ('TimbraCFDI', 'ObtieneCFDI', 'CancelaCFDIAck', 'ConsultaEstatusSat')}):
            raise RuntimeError('Non-QA request blocked')
        if len(report['requests']) >= 16:
            raise RuntimeError('Pilot request limit reached')
        entry = {'operation': target.path.rsplit('/', 1)[-1], 'outcome': 'unknown'}
        report['requests'].append(entry)
        number = len(report['requests'])
        save(f'{number:02}-request.json', kwargs.get('json'))
        save('report.json', report)  # Persist intent before contacting PAC.
        kwargs['allow_redirects'] = False
        kwargs['timeout'] = (10, 45)
        response = original_request(session, method, url, **kwargs)
        entry.update(http_status=response.status_code, outcome='response',
            response_sha256=hashlib.sha256(response.content).hexdigest())
        save(f'{number:02}-response.json', response.json())
        save('report.json', report)
        return response

    requests.sessions.Session.request = guarded_request
    engine = create_engine('sqlite:///' + str(folder / 'pilot.sqlite'))
    models = (ProductCatalog, BillingDraft, BillingDraftItem, BillingPayment, PaymentReceipt,
        PaymentAct, BillingCancellation, CancellationAct, FiscalRevision, FiscalRevisionAct, FiscalAct)
    Base.metadata.create_all(engine, tables=[model.__table__ for model in models])
    ctx = SecurityContext(tenant_id='public-pac-qa', user_id='local-pilot', role='operator')

    def check(name, condition, **data):
        report['steps'].append({'name': name, 'passed': bool(condition), **data})
        save('report.json', report)
        if not condition:
            raise RuntimeError(name)

    try:
        with Session(engine) as db:
            item = commercial._resolve_item_payload(db, ctx.tenant_id, DraftItemInput(
                description='PRUEBA QA COTIZA SIN VALIDEZ FISCAL', quantity=1, unit_price=100,
                sat_product_code='80101500', unit_code='E48', tax_rate=0.16))
            draft = BillingDraft(id=str(uuid4()), company_id=ctx.tenant_id, stamp_status='not_sent',
                emitter_rfc='IIA040805DZ4', emitter_name='INDISTRIA ILUMINADORA DE ALMACENES',
                emitter_regimen='626', place_of_issue='32690', customer_rfc='EKU9003173C9',
                customer_name='ESCUELA KEMPER URGATE', customer_zip='42501', customer_regimen='603',
                customer_use_cfdi='G03', currency='MXN', payment_method='PPD', payment_form='99', items=[item])
            commercial._recalculate_draft(draft)
            db.add(draft); db.commit()
            xml = commercial._build_cfdi_xml(draft)
            check('invoice_unsigned_xsd', fiscal.schema_valid(xml))
            result = pac.timbra_cfdi(base64.b64encode(xml.encode()).decode(), 'qa-'+draft.id)
            issued = pac.extract_stamped_document(result)
            stamped = issued.get('stamped_xml') or ''
            check('invoice_pac_accepted', issued['success'], provider_code=result['provider_response'].get('Codigo'),
                provider_message=result['provider_response'].get('Mensaje'))
            check('invoice_signed_xsd', fiscal.schema_valid(stamped, signed=True))
            draft.stamped_xml_base64 = base64.b64encode(stamped.encode()).decode()
            draft.stamp_status = 'stamped'; draft.status = 'stamped'; draft.stamped_at = datetime.utcnow()
            db.commit()
            invoice_uuid = pac.extract_stamp_uuid(stamped)
            report['invoice_uuid'] = invoice_uuid
            save('invoice.xml.json', {'xml': stamped})
            # Use a valid alternate use on the vendor demo identity; preserve all economics.
            if args.scenario == 'substitutions':
                request = CreateFiscalRevision(source_uuid=invoice_uuid, idempotency_key=uuid4(),
                    reason='Ensayo QA publico de sustitucion sin cambios economicos', changes={'customer_use_cfdi': 'G01'})
                revisions.action(draft.id, 'create', request, ctx, db)
                corrected = revisions.action(draft.id, 'stamp', None, ctx, db)
                check('invoice_substitute_issued', corrected['history'][0]['status'] == 'stamped')
                revisions.action(draft.id, 'cancel-original', None, ctx, db)
                corrected = revisions.action(draft.id, 'confirm-cancellation', None, ctx, db)
                check('invoice_substitute_active', not corrected['pending'] and corrected['active_uuid'] != invoice_uuid)
            first_id = None
            for sequence in (1, 2):
                declared = payments.register_payment(draft.id, RegisterPayment(amount='58.00',
                    paid_at=datetime.now(timezone.utc), payment_form='03',
                    reference=f'QA-{draft.id}-{sequence}', idempotency_key=uuid4()), ctx, db)
                if args.scenario == 'payments-recovery' and sequence == 2:
                    original_sender, captured = pac.timbra_cfdi, {}
                    def lose_response(**kwargs):
                        response = original_sender(**kwargs)
                        info = pac.extract_stamped_document(response)
                        if not info['success']: return response
                        captured['uuid'] = pac.extract_stamp_uuid(info['stamped_xml'])
                        raise TimeoutError('Intentional local response loss after QA acceptance')
                    pac.timbra_cfdi = lose_response
                    try:
                        payments.stamp_receipt(draft.id, declared['id'], ctx, db)
                    except HTTPException as exc:
                        if exc.status_code != 502: raise
                    finally: pac.timbra_cfdi = original_sender
                    pending = payments.list_payments(draft.id, ctx, db)
                    check('cash_zero_rep_pending', pending['balance'] == '0.00' and not pending['fiscal_complete']
                        and pending['payments'][-1]['receipt_status'] == 'unknown' and bool(captured.get('uuid')))
                    recovered = payments.recover_receipt(draft.id, declared['id'], RecoverReceipt(receipt_uuid=captured['uuid']), ctx, db)
                    count = len(report['requests'])
                    repeated = payments.recover_receipt(draft.id, declared['id'], RecoverReceipt(receipt_uuid=captured['uuid']), ctx, db)
                    check('rep2_recovered_idempotently_without_reissue', recovered['receipt_status'] == repeated['receipt_status'] == 'stamped'
                        and count == len(report['requests']))
                    emitted = {'ok': True, **recovered}
                else:
                    emitted = payments.stamp_receipt(draft.id, declared['id'], ctx, db)
                    recovered = pac.extract_stamped_document(pac.retrieve_cfdi(draft.emitter_rfc, emitted['receipt_uuid']))
                    receipt = db.query(PaymentReceipt).filter_by(payment_id=declared['id']).one()
                    check(f'rep{sequence}_retrieved_without_reissue', recovered['success'] and
                        pac.extract_stamp_uuid(recovered['stamped_xml']) == emitted['receipt_uuid'] and
                        payments.service.validate_returned_xml(receipt.request_xml, recovered['stamped_xml']))
                check(f'rep{sequence}_issued', emitted['ok'], uuid=emitted.get('receipt_uuid'))
                if sequence == 1 and args.scenario == 'substitutions':
                    first_id = declared['id']
                    change = CreateFiscalRevision(source_uuid=emitted['receipt_uuid'], idempotency_key=uuid4(),
                        reason='Ensayo QA de referencia de pago', changes={'operation_reference': 'QA-CORREGIDA'})
                    revisions.action(draft.id, 'create', change, ctx, db, first_id)
                    replacement = revisions.action(draft.id, 'stamp', None, ctx, db, first_id)
                    check('rep1_substitute_issued', replacement['history'][0]['status'] == 'stamped')
                    revisions.action(draft.id, 'cancel-original', None, ctx, db, first_id)
                    replacement = revisions.action(draft.id, 'confirm-cancellation', None, ctx, db, first_id)
                    check('rep1_substitute_active', not replacement['pending'])
            summary = payments.list_payments(draft.id, ctx, db)
            check('cash_preserved_balance_zero', summary['paid'] == '116.00' and summary['balance'] == '0.00'
                and db.query(BillingPayment).count() == 2 and draft.stamped_xml_base64 == base64.b64encode(stamped.encode()).decode())
            if args.scenario == 'payments-recovery':
                check('fiscal_completion_after_recovery', summary['fiscal_complete'] and summary['receipts_pending_count'] == 0)
            else:
                cancellations.request(draft.id, CancelDocument(idempotency_key=uuid4(), reason='02'), ctx, db, declared['id'])
                state = cancellations.reconcile(draft.id, ctx, db, declared['id'])
                check('rep2_cancelled', state['status'] == 'cancelled')
            report['result'] = 'passed'
    except Exception as exc:
        report['result'] = 'stopped'
        report['error'] = {'type': type(exc).__name__, 'detail': str(getattr(exc, 'detail', exc))[:1500]}
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save('report.json', report)
        requests.sessions.Session.request = original_request
        engine.dispose()
    print(json.dumps({k: v for k, v in report.items() if k != 'steps'}, indent=2))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
