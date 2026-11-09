"""Synthetic fiscal replacements. Original documents and economic payments persist."""
import base64
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import Mock
from xml.etree import ElementTree as ET
import pytest
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.api.routers import fiscal_revisions_v1 as api, payments_v1 as payments
from app.services import fiscal_revisions as svc
from app.models.fiscal_revision import FiscalRevision, FiscalRevisionAct
from app.models.billing_payment import BillingPayment, PaymentReceipt
from app.models.commercial import BillingDraft
from app.schemas.fiscal_revision import CreateFiscalRevision
from app.schemas.billing_cancellation import RecoverReceipt
from test_payment_cycle import db, seed, record, stamp, body, accepted as accepted_rep, CTX, no_network
from test_billing_cancellations import ack, status
from test_billing_stamp_integration import pg_engine


@pytest.fixture(autouse=True)
def guards(monkeypatch):
    monkeypatch.setattr(api.settings, 'FISCAL_CORRECTIONS_ENABLED', True)
    monkeypatch.setattr(api.settings, 'FISCAL_CANCELLATION_ENABLED', True)
    monkeypatch.setattr(api.settings, 'TIMBRACFDI_USE_SOAP', False)


def accepted(**kwargs):
    root = ET.fromstring(base64.b64decode(kwargs['xml_base64']))
    if root.find('c:Complemento', svc.NS) is None: ET.SubElement(root, '{'+svc.C+'}Complemento')
    return accepted_rep(xml_base64=base64.b64encode(ET.tostring(root)).decode())


def seed_valid(db):
    invoice = seed(db)
    root = ET.fromstring(base64.b64decode(invoice.stamped_xml_base64))
    root.remove(root.find('c:Complemento', svc.NS))
    # The synthetic signer needs the complement element to append its synthetic TFD.
    ET.SubElement(root, '{'+svc.C+'}Complemento')
    result = accepted(xml_base64=base64.b64encode(ET.tostring(root)).decode())
    invoice.stamped_xml_base64 = base64.b64encode(result['provider_response']['Xml'].encode()).decode()
    db.commit()
    assert svc.schema_valid(base64.b64decode(invoice.stamped_xml_base64).decode(), signed=True)
    return invoice


def request(db, changes=None, payment_id=None, **overrides):
    invoice = db.query(BillingDraft).filter_by(id='credit-invoice').one()
    xml, _, _ = svc.context(db, invoice, payment_id)
    return CreateFiscalRevision(**{'source_uuid': payments.pac.extract_stamp_uuid(xml), 'idempotency_key':str(uuid4()),
        'reason':'Corrección sintética revisada por operador',
        'changes':changes or {'customer_name':'NOMBRE SINTETICO CORREGIDO'}, **overrides})


def action(db, operation, payload=None, payment_id=None):
    return api.action('credit-invoice', operation, payload, CTX, db, payment_id)


def wire_provider(monkeypatch):
    mock = Mock(side_effect=accepted); monkeypatch.setattr(api.pac, 'timbra_cfdi', mock)
    monkeypatch.setattr(api.pac, 'cancel_cfdi', Mock(side_effect=lambda emitter, folio, reason, replacement: ack(folio, emitter)))
    monkeypatch.setattr(api.pac, 'query_cfdi_status', Mock(side_effect=status))
    return mock


def test_invoice_replacement_activates_only_after_original_cancellation(db, monkeypatch):
    invoice = seed_valid(db); original = invoice.stamped_xml_base64
    pac = wire_provider(monkeypatch); payload = request(db)
    prepared = action(db, 'create', payload)
    assert prepared['pending'] and prepared['active_uuid'] == str(payload.source_uuid)
    assert action(db, 'create', payload)['history'][0]['id'] == prepared['history'][0]['id']
    with pytest.raises(HTTPException): record(db)
    with pytest.raises(HTTPException): action(db, 'cancel-original')
    issued = action(db, 'stamp')
    replacement_uuid = issued['history'][0]['result_uuid']
    assert issued['active_uuid'] == str(payload.source_uuid)
    assert action(db, 'cancel-original')['history'][0]['source_cancellation_status'] == 'requested'
    assert action(db, 'read')['pending'] is True
    confirmed = action(db, 'confirm-cancellation')
    assert confirmed['active_uuid'] == replacement_uuid and not confirmed['pending']
    assert invoice.stamped_xml_base64 == original
    assert svc.parse(base64.b64decode(confirmed['active_xml_base64']).decode()).find('c:CfdiRelacionados/c:CfdiRelacionado',svc.NS).get('UUID') == str(payload.source_uuid)
    payment = record(db, body('58', paid_at=datetime.now(timezone.utc)))
    stamp(db, payment['id'])
    assert payments.list_payments(invoice.id, CTX, db)['invoice_uuid'] == replacement_uuid
    assert db.query(BillingPayment).count() == 1 and pac.call_count == 2


def test_receipt_form_correction_preserves_cash_and_allows_next_partiality(db, monkeypatch):
    invoice = seed_valid(db); pac = wire_provider(monkeypatch)
    item = record(db); stamp(db, item['id'])
    receipt = db.query(PaymentReceipt).one(); original = receipt.xml_base64
    payload = request(db, {'payment_form':'02', 'operation_reference':'REF-CORREGIDA'}, item['id'])
    prepared = action(db, 'create', payload, item['id'])
    xml = svc.parse(base64.b64decode(prepared['history'][0]['request_xml_base64']).decode())
    pago = xml.find('c:Complemento/p:Pagos/p:Pago', svc.NS)
    assert pago.get('FormaDePagoP') == '02' and pago.get('Monto') == '58.00'
    action(db, 'stamp', payment_id=item['id']); action(db, 'cancel-original', payment_id=item['id'])
    assert not payments.read_receipt(invoice.id,item['id'],CTX,db)['fiscal_ready']
    updated = action(db, 'confirm-cancellation', payment_id=item['id'])
    assert not updated['pending'] and receipt.xml_base64 == original
    view = payments.read_receipt(invoice.id,item['id'],CTX,db)
    assert view['fiscal_ready'] and view['has_active_revision']
    assert payments.list_payments(invoice.id,CTX,db)['balance'] == '58.00'
    second = record(db, body('58')); stamp(db, second['id'])
    assert payments.list_payments(invoice.id,CTX,db)['balance'] == '0.00'
    assert db.query(BillingPayment).count() == 2


def test_repeated_invoice_correction_forms_a_chain_without_rewriting_sources(db, monkeypatch):
    invoice = seed_valid(db); original = invoice.stamped_xml_base64; wire_provider(monkeypatch)
    for index in range(2):
        action(db,'create',request(db,{'customer_name':f'CORRECCION SINTETICA {index}'}))
        action(db,'stamp'); action(db,'cancel-original'); action(db,'confirm-cancellation')
    view=action(db,'read')
    assert len(view['history']) == 2 and not view['pending']
    assert view['history'][1]['source_uuid'] == view['history'][0]['result_uuid']
    assert invoice.stamped_xml_base64 == original


def test_unknown_revision_recovered_without_another_stamp(db, monkeypatch):
    seed_valid(db); action(db,'create',request(db))
    pac=Mock(side_effect=TimeoutError()); monkeypatch.setattr(api.pac,'timbra_cfdi',pac)
    assert action(db,'stamp')['history'][0]['status'] == 'unknown'
    with pytest.raises(HTTPException): action(db,'stamp')
    with pytest.raises(HTTPException): action(db,'abandon')
    revision=db.query(FiscalRevision).one()
    issued=accepted(xml_base64=base64.b64encode(revision.request_xml.encode()).decode())
    folio=api.pac.extract_stamp_uuid(issued['provider_response']['Xml'])
    monkeypatch.setattr(api.pac,'retrieve_cfdi',Mock(return_value=issued))
    assert action(db,'reconcile',RecoverReceipt(receipt_uuid=folio))['history'][0]['status'] == 'stamped'
    assert pac.call_count == 1


def test_processing_revision_cannot_recover_before_original_worker_returns(db, monkeypatch):
    seed_valid(db); action(db,'create',request(db))
    retrieve = Mock(); monkeypatch.setattr(api.pac,'retrieve_cfdi',retrieve)
    def issue(**kwargs):
        # The reservation has been committed while the original provider call is running.
        with pytest.raises(HTTPException) as exc:
            action(db,'reconcile',RecoverReceipt(receipt_uuid=str(uuid4())))
        assert exc.value.status_code == 409
        retrieve.assert_not_called()
        return accepted(**kwargs)
    provider = Mock(side_effect=issue); monkeypatch.setattr(api.pac,'timbra_cfdi',provider)
    assert action(db,'stamp')['history'][0]['status'] == 'stamped'
    assert provider.call_count == 1
    retrieve.assert_not_called()


def test_abandon_unissued_revision_preserves_acts_and_accepts_new_correction(db):
    seed_valid(db); first=request(db); action(db,'create',first)
    assert action(db,'abandon')['pending'] is False
    action(db,'create',request(db,{'customer_zip':'01000'}))
    assert db.query(FiscalRevision).count() == 2
    assert db.query(FiscalRevision).filter_by(status='abandoned').count() == 1
    assert db.query(FiscalRevisionAct).count() == 3


def test_wrong_returned_xml_stays_unknown(db, monkeypatch):
    seed_valid(db); action(db,'create',request(db))
    def changed(**kwargs):
        value=accepted(**kwargs)
        value['provider_response']['Xml']=value['provider_response']['Xml'].replace('Total="116.00"','Total="117.00"')
        return value
    monkeypatch.setattr(api.pac,'timbra_cfdi',Mock(side_effect=changed))
    assert action(db,'stamp')['history'][0]['status'] == 'unknown'


def test_paid_invoice_and_nonlast_receipt_cannot_be_rewritten(db, monkeypatch):
    seed_valid(db); wire_provider(monkeypatch)
    first=record(db); stamp(db,first['id'])
    with pytest.raises(HTTPException): action(db,'create',request(db))
    record(db,body('10'))
    with pytest.raises(HTTPException): action(db,'create',request(db,{'payment_form':'02'},first['id']),first['id'])


@pytest.mark.parametrize('extra', [{'amount':'1'},{'draft_id':'foreign'},{'tenant_id':'foreign'}])
def test_economic_and_scope_injection_is_rejected(extra):
    with pytest.raises(ValidationError): CreateFiscalRevision(idempotency_key=uuid4(),source_uuid=uuid4(),reason='Synthetic correction',changes={'customer_name':'TEST',**extra})


def test_http_guards_before_provider(db, monkeypatch):
    from app.db.session import get_db
    from app.services.jwt_service import create_access_token
    seed_valid(db); app=FastAPI(); app.include_router(api.router)
    def override(): yield db
    app.dependency_overrides[get_db]=override; client=TestClient(app)
    provider=Mock();monkeypatch.setattr(api.pac,'timbra_cfdi',provider)
    path='/v1/billing/drafts/credit-invoice/correction'
    def headers(role='operator',tenant=CTX.tenant_id): return {'Authorization':'Bearer '+create_access_token({'sub':'synthetic','tenant':tenant,'role':role})}
    payload=request(db).model_dump(mode='json')
    assert client.post(path,json=payload).status_code == 401
    assert client.post(path,json=payload,headers=headers('viewer')).status_code == 403
    assert client.post(path,json=payload,headers=headers(tenant='foreign')).status_code == 404
    monkeypatch.setattr(api.settings,'FISCAL_CORRECTIONS_ENABLED',False)
    assert client.post(path,json=payload,headers=headers()).status_code == 503
    provider.assert_not_called()


def test_postgres_simultaneous_create_and_stamp_only_issues_once(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    from test_payment_cycle import TABLES
    Base.metadata.create_all(pg_engine, tables=TABLES)
    with sessionmaker(bind=pg_engine)() as db:
        seed_valid(db); payload=request(db)
    barrier=Barrier(2)
    def create(_):
        with sessionmaker(bind=pg_engine)() as db:
            barrier.wait(10); return action(db,'create',payload)['history'][0]['id']
    with ThreadPoolExecutor(max_workers=2) as pool: ids=list(pool.map(create,[1,2]))
    assert ids[0] == ids[1]
    entered, release=Event(),Event()
    def issue(**kwargs):
        entered.set(); assert release.wait(10); return accepted(**kwargs)
    provider=Mock(side_effect=issue); monkeypatch.setattr(api.pac,'timbra_cfdi',provider)
    retrieve=Mock(); monkeypatch.setattr(api.pac,'retrieve_cfdi',retrieve)
    def emit():
        with sessionmaker(bind=pg_engine)() as db: return action(db,'stamp')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(emit)
        try:
            assert entered.wait(10)
            with sessionmaker(bind=pg_engine)() as db:
                with pytest.raises(HTTPException) as exc: action(db,'stamp')
                assert exc.value.status_code == 409
            with sessionmaker(bind=pg_engine)() as db:
                with pytest.raises(HTTPException) as exc:
                    action(db,'reconcile',RecoverReceipt(receipt_uuid=str(uuid4())))
                assert exc.value.status_code == 409
                retrieve.assert_not_called()
        finally: release.set()
        assert future.result(10)['history'][0]['status'] == 'stamped'
    assert provider.call_count == 1


def test_postgres_correction_migration_and_evidence_guards(pg_engine):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import text, inspect
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.db.base import Base
    from test_payment_cycle import TABLES
    Base.metadata.create_all(pg_engine,tables=[t for t in TABLES if t not in [FiscalRevision.__table__,FiscalRevisionAct.__table__]])
    path=Path(__file__).resolve().parents[1]/'migrations/versions/20261107_02_fiscal_revisions.py'
    spec=importlib.util.spec_from_file_location('fiscal_revision_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pg_engine.begin() as conn:
        module.op=Operations(MigrationContext.configure(conn));module.upgrade();module.upgrade()
    assert inspect(pg_engine).has_table('billing_fiscal_revisions')
    with sessionmaker(bind=pg_engine)() as db:
        seed_valid(db);action(db,'create',request(db))
    for statement in ('UPDATE billing_fiscal_revision_acts SET sequence=sequence',
                      'DELETE FROM billing_fiscal_revision_acts','TRUNCATE billing_fiscal_revision_acts CASCADE'):
        with pytest.raises(DBAPIError,match='append-only'):
            with pg_engine.begin() as conn:conn.execute(text(statement))
    with pytest.raises(DBAPIError,match='foreign key'):
        with pg_engine.begin() as conn:
            conn.execute(text("INSERT INTO billing_fiscal_revision_acts (id,company_id,revision_id,sequence,payload,content_hash) VALUES ('foreign','foreign','foreign',1,'{}','synthetic')"))


def test_no_evidence_or_changed_request_cannot_be_sent(db, monkeypatch):
    from sqlalchemy import text
    seed_valid(db);action(db,'create',request(db))
    provider=Mock();monkeypatch.setattr(api.pac,'timbra_cfdi',provider)
    db.execute(text('DELETE FROM billing_fiscal_revision_acts'));db.commit()
    with pytest.raises(HTTPException):action(db,'stamp')
    provider.assert_not_called()


def test_empty_provider_success_is_unknown_not_an_issued_replacement(db, monkeypatch):
    seed_valid(db);action(db,'create',request(db))
    provider=Mock(return_value={'ok':True,'status_code':200,'provider_response':{'Codigo':0}})
    monkeypatch.setattr(api.pac,'timbra_cfdi',provider)
    assert action(db,'stamp')['history'][0]['status'] == 'unknown'
    with pytest.raises(HTTPException):action(db,'stamp')
    assert provider.call_count == 1
