"""Synthetic cancellation/recovery acceptance, with external networking forbidden."""
import base64
import json
from uuid import uuid4
from unittest.mock import Mock
from xml.etree import ElementTree as ET
import pytest
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from app.api.routers import cancellations_v1 as api, payments_v1 as payments
from app.models.billing_cancellation import BillingCancellation, CancellationAct
from app.models.billing_payment import BillingPayment, PaymentReceipt
from app.models.commercial import BillingDraft
from app.schemas.billing_cancellation import CancelDocument, RecoverReceipt
from app.services import billing_cancellations as svc
from test_payment_cycle import db, seed, record, stamp, accepted, CTX, no_network, body
from test_billing_stamp_integration import pg_engine


@pytest.fixture(autouse=True)
def guards(monkeypatch):
    monkeypatch.setattr(api.settings, 'FISCAL_CANCELLATION_ENABLED', True)
    monkeypatch.setattr(api.settings, 'TIMBRACFDI_USE_SOAP', False)


def cancel_body(**changes):
    return CancelDocument(**{'idempotency_key': str(uuid4()), 'reason': '03', **changes})


def ack(folio, emitter='EKU9003173C9'):
    return {'ok': True, 'status_code': 200, 'provider_response': {'CodigoSat': '0',
        'XmlAcuse': f'<Acuse RfcEmisor="{emitter}"><Folios><UUID>{folio}</UUID><EstatusUUID>201</EstatusUUID></Folios></Acuse>'}}


def status(folio, state='Cancelado', cancellation=''):
    return {'ok': True, 'status_code': 200, 'provider_response': {'Codigo': 0, 'RespuestaSat': {
        'UUID': folio, 'CodigoEstatusSat': 'S - Comprobante obtenido satisfactoriamente.',
        'EstadoSat': state, 'EstatusCancelacionSat': cancellation}}}


def request(db, payload=None): return api.cancel_invoice('credit-invoice', payload or cancel_body(), CTX, db)


def test_ack_is_request_until_matching_sat_confirmation(db, monkeypatch):
    original = seed(db); folio = svc.target(db, original)[1]
    cancel = Mock(return_value=ack(folio, original.emitter_rfc)); monkeypatch.setattr(api.pac, 'cancel_cfdi', cancel)
    payload = cancel_body()
    assert request(db, payload)['status'] == 'requested'
    assert request(db, payload)['status'] == 'requested' and cancel.call_count == 1
    assert original.stamp_status == 'stamped' and original.stamped_xml_base64
    with pytest.raises(HTTPException): record(db)
    query = Mock(return_value=status(folio, 'Vigente', 'En proceso')); monkeypatch.setattr(api.pac, 'query_cfdi_status', query)
    assert api.reconcile_invoice(original.id, CTX, db)['status'] == 'requested'
    query.return_value = status(str(uuid4()))
    assert api.reconcile_invoice(original.id, CTX, db)['status'] == 'requested'
    query.return_value = status(folio)
    assert api.reconcile_invoice(original.id, CTX, db)['status'] == 'cancelled'
    assert api.reconcile_invoice(original.id, CTX, db)['status'] == 'cancelled'
    assert query.call_count == 3 and cancel.call_count == 1
    monkeypatch.setattr(api.settings, 'FISCAL_CANCELLATION_ENABLED', False)
    with pytest.raises(HTTPException): record(db)  # Disabling UI never reactivates a cancelled invoice.


def test_unknown_result_never_repeats_cancellation(db, monkeypatch):
    seed(db); provider = Mock(side_effect=TimeoutError()); monkeypatch.setattr(api.pac, 'cancel_cfdi', provider)
    payload = cancel_body()
    assert request(db, payload)['status'] == 'unknown'
    assert request(db, payload)['status'] == 'unknown' and provider.call_count == 1
    with pytest.raises(HTTPException): request(db, cancel_body(reason='02'))
    assert db.query(BillingCancellation).count() == 1


@pytest.mark.parametrize('kind', ['bad_uuid', 'malformed', 'http500', 'bare_success'])
def test_ambiguous_ack_does_not_claim_cancelled(db, monkeypatch, kind):
    original = seed(db); result = ack(svc.target(db, original)[1], original.emitter_rfc)
    if kind == 'bad_uuid': result = ack(str(uuid4()), original.emitter_rfc)
    if kind == 'malformed': result['provider_response']['XmlAcuse'] = '<bad'
    if kind == 'http500': result['status_code'] = 500
    if kind == 'bare_success': result['provider_response'] = {'Codigo': 0, 'Mensaje': 'Cancelado'}
    monkeypatch.setattr(api.pac, 'cancel_cfdi', Mock(return_value=result))
    assert request(db)['status'] == 'unknown'


def test_invoice_with_payments_cannot_be_cancelled(db, monkeypatch):
    seed(db); record(db); provider = Mock(); monkeypatch.setattr(api.pac, 'cancel_cfdi', provider)
    with pytest.raises(HTTPException, match='') as exc: request(db)
    assert exc.value.status_code == 409
    assert db.query(BillingPayment).count() == 1 and db.query(BillingCancellation).count() == 0
    provider.assert_not_called()


def test_receipt_cancel_preserves_money_and_stops_fiscal_progress(db, monkeypatch):
    invoice = seed(db); monkeypatch.setattr(payments.pac, 'timbra_cfdi', Mock(side_effect=accepted))
    item = record(db); stamp(db, item['id'])
    receipt = db.query(PaymentReceipt).one()
    monkeypatch.setattr(api.pac, 'cancel_cfdi', Mock(return_value=ack(receipt.uuid, invoice.emitter_rfc)))
    assert api.cancel_receipt(invoice.id, item['id'], cancel_body(), CTX, db)['status'] == 'requested'
    monkeypatch.setattr(api.pac, 'query_cfdi_status', Mock(return_value=status(receipt.uuid.lower())))
    assert api.reconcile_receipt(invoice.id, item['id'], CTX, db)['status'] == 'cancelled'
    assert payments.list_payments(invoice.id, CTX, db)['balance'] == '58.00'
    assert receipt.status == 'stamped' and receipt.xml_base64
    second = record(db, body('58.00'))
    with pytest.raises(HTTPException): stamp(db, second['id'])
    assert db.query(BillingPayment).count() == 2


def test_substitution_requires_same_tenant_related_stamped_document(db, monkeypatch):
    original = seed(db); root, folio, _ = svc.target(db, original)
    replacement_id = str(uuid4()); replacement_uuid = str(uuid4())
    root.find('c:Complemento', svc.NS)[0].set('UUID', replacement_uuid)
    ET.SubElement(ET.SubElement(root, '{'+svc.NS['c']+'}CfdiRelacionados', {'TipoRelacion': '04'}),
        '{'+svc.NS['c']+'}CfdiRelacionado', {'UUID': folio})
    replacement = BillingDraft(id=replacement_id, company_id=CTX.tenant_id, stamp_status='stamped',
        stamped_xml_base64=base64.b64encode(ET.tostring(root)).decode(), emitter_rfc=original.emitter_rfc,
        customer_rfc=original.customer_rfc)
    db.add(replacement); db.commit()
    provider = Mock(return_value=ack(folio, original.emitter_rfc)); monkeypatch.setattr(api.pac, 'cancel_cfdi', provider)
    response = request(db, cancel_body(reason='01', replacement_draft_id=replacement_id))
    assert response['replacement_uuid'] == replacement_uuid
    assert provider.call_args.args == (original.emitter_rfc, folio, '01', replacement_uuid)


@pytest.mark.parametrize('changes', [{'reason':'04'}, {'reason':'01'}, {'reason':'03','replacement_draft_id':str(uuid4())}, {'tenant_id':'foreign'}])
def test_strict_cancellation_input(changes):
    with pytest.raises(ValidationError): cancel_body(**changes)


def test_http_auth_and_scope_before_provider(db, monkeypatch):
    from app.db.session import get_db
    from app.services.jwt_service import create_access_token
    seed(db); app = FastAPI(); app.include_router(api.router)
    def override(): yield db
    app.dependency_overrides[get_db] = override; client = TestClient(app)
    provider = Mock(); monkeypatch.setattr(api.pac, 'cancel_cfdi', provider)
    path = '/v1/billing/drafts/credit-invoice/cancellation'
    def headers(role='operator', tenant=CTX.tenant_id):
        return {'Authorization':'Bearer '+create_access_token({'sub':'synthetic','tenant':tenant,'role':role})}
    payload = cancel_body().model_dump(mode='json')
    assert client.post(path, json=payload).status_code == 401
    assert client.post(path, json=payload, headers=headers('viewer')).status_code == 403
    assert client.post(path, json=payload, headers=headers(tenant='foreign')).status_code == 404
    monkeypatch.setattr(api.settings, 'TIMBRACFDI_ENV', 'production')
    assert client.post(path, json=payload, headers=headers()).status_code == 503
    provider.assert_not_called()


def test_recover_unknown_receipt_by_exact_provider_xml_without_reissue(db, monkeypatch):
    invoice = seed(db); item = record(db)
    provider = Mock(side_effect=TimeoutError()); monkeypatch.setattr(payments.pac, 'timbra_cfdi', provider)
    with pytest.raises(HTTPException): stamp(db, item['id'])
    receipt = db.query(PaymentReceipt).one()
    returned = accepted(xml_base64=base64.b64encode(receipt.request_xml.encode()).decode())
    receipt_uuid = payments.pac.extract_stamp_uuid(returned['provider_response']['Xml'])
    retrieve = Mock(return_value=returned); monkeypatch.setattr(payments.pac, 'retrieve_cfdi', retrieve)
    result = payments.recover_receipt(invoice.id, item['id'], RecoverReceipt(receipt_uuid=receipt_uuid), CTX, db)
    assert result['receipt_status'] == 'stamped' and result['receipt_uuid'] == receipt_uuid
    assert db.query(BillingPayment).count() == 1 and provider.call_count == 1 and retrieve.call_count == 1
    assert payments.list_payments(invoice.id, CTX, db)['balance'] == '58.00'
    assert payments.recover_receipt(invoice.id, item['id'], RecoverReceipt(receipt_uuid=receipt_uuid), CTX, db)['receipt_status'] == 'stamped'
    assert retrieve.call_count == 1


def test_recovery_rejects_wrong_business_xml(db, monkeypatch):
    invoice = seed(db); item = record(db)
    monkeypatch.setattr(payments.pac, 'timbra_cfdi', Mock(side_effect=TimeoutError()))
    with pytest.raises(HTTPException): stamp(db, item['id'])
    receipt = db.query(PaymentReceipt).one()
    result = accepted(xml_base64=base64.b64encode(receipt.request_xml.encode()).decode())
    folio = payments.pac.extract_stamp_uuid(result['provider_response']['Xml'])
    result['provider_response']['Xml'] = result['provider_response']['Xml'].replace('ImpPagado="58.00"', 'ImpPagado="1.00"')
    monkeypatch.setattr(payments.pac, 'retrieve_cfdi', Mock(return_value=result))
    with pytest.raises(HTTPException): payments.recover_receipt(invoice.id, item['id'], RecoverReceipt(receipt_uuid=folio), CTX, db)
    assert receipt.status == 'unknown'


def test_failed_evidence_after_network_retains_durable_reservation(db, monkeypatch):
    original = seed(db); folio = svc.target(db, original)[1]
    monkeypatch.setattr(api.pac, 'cancel_cfdi', Mock(return_value=ack(folio, original.emitter_rfc)))
    append = svc.append_act
    def fail_after(db, row, ctx, action, before, evidence=None):
        if action == 'provider_response': raise RuntimeError('synthetic storage failure')
        return append(db, row, ctx, action, before, evidence)
    monkeypatch.setattr(svc, 'append_act', fail_after)
    with pytest.raises(RuntimeError): request(db)
    db.rollback()
    assert db.query(BillingCancellation).one().status == 'processing'
    assert db.query(CancellationAct).count() == 1


def test_postgres_migration_and_two_concurrent_requests(pg_engine, monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.db.base import Base
    from app.models.billing_payment import PaymentAct
    Base.metadata.create_all(pg_engine, tables=[BillingPayment.__table__, PaymentReceipt.__table__, PaymentAct.__table__])
    path = Path(__file__).resolve().parents[1]/'migrations/versions/20261107_01_cancellations.py'
    spec = importlib.util.spec_from_file_location('cancellation_migration', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with pg_engine.begin() as conn:
        module.op = Operations(MigrationContext.configure(conn)); module.upgrade(); module.upgrade()
    with sessionmaker(bind=pg_engine)() as db: invoice = seed(db); folio = svc.target(db, invoice)[1]
    entered, release = Event(), Event(); payload = cancel_body()
    def provider(*args):
        entered.set(); assert release.wait(10); return ack(folio)
    provider_mock = Mock(side_effect=provider); monkeypatch.setattr(api.pac, 'cancel_cfdi', provider_mock)
    def attempt():
        with sessionmaker(bind=pg_engine)() as db: return request(db, payload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(attempt)
        try:
            assert entered.wait(10)
            with sessionmaker(bind=pg_engine)() as db: assert request(db, payload)['status'] == 'processing'
        finally: release.set()
        assert future.result(10)['status'] == 'requested'
    assert provider_mock.call_count == 1
    for statement in ('UPDATE billing_cancellation_acts SET sequence=sequence', 'DELETE FROM billing_cancellation_acts', 'TRUNCATE billing_cancellation_acts CASCADE'):
        with pytest.raises(DBAPIError, match='append-only'):
            with pg_engine.begin() as conn: conn.execute(text(statement))


def test_cancel_receipts_in_reverse_order_after_confirmed_later_cancellation(db, monkeypatch):
    invoice = seed(db); monkeypatch.setattr(payments.pac, 'timbra_cfdi', Mock(side_effect=accepted))
    first = record(db); stamp(db, first['id'])
    second = record(db, body('58')); stamp(db, second['id'])
    provider = Mock(side_effect=lambda emitter, folio, reason, replacement_uuid: ack(folio, emitter))
    monkeypatch.setattr(api.pac, 'cancel_cfdi', provider)
    monkeypatch.setattr(api.pac, 'query_cfdi_status', Mock(side_effect=status))
    with pytest.raises(HTTPException): api.cancel_receipt(invoice.id, first['id'], cancel_body(), CTX, db)
    api.cancel_receipt(invoice.id, second['id'], cancel_body(), CTX, db)
    api.reconcile_receipt(invoice.id, second['id'], CTX, db)
    assert api.cancel_receipt(invoice.id, first['id'], cancel_body(), CTX, db)['status'] == 'requested'
    assert payments.list_payments(invoice.id, CTX, db)['paid'] == '116.00'
