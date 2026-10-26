"""Synthetic PPD/REP tests: no network, no real PAC or customer data."""
import base64
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from unittest.mock import Mock
from xml.etree import ElementTree as ET
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.base import Base
from app.models.commercial import ProductCatalog, BillingDraft, BillingDraftItem
from app.models.billing_payment import BillingPayment, PaymentReceipt, PaymentAct
from app.models.fiscal_act import FiscalAct
from app.api.routers import payments_v1 as api, commercial_v1 as commercial
from app.schemas.billing_payment import RegisterPayment
from app.services import payment_receipts as svc
from test_billing_stamp_integration import CTX, pg_engine  # guarded disposable PG fixture

TABLES = [ProductCatalog.__table__, BillingDraft.__table__, BillingDraftItem.__table__, FiscalAct.__table__,
          BillingPayment.__table__, PaymentReceipt.__table__, PaymentAct.__table__]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('External network forbidden')
    monkeypatch.setattr('requests.sessions.Session.request',forbidden)
    monkeypatch.setattr(api.settings,'FISCAL_PPD_ENABLED',True)
    monkeypatch.setattr(api.settings,'FISCAL_REP_STAMP_ENABLED',True)
    monkeypatch.setattr(api.settings,'TIMBRACFDI_ENV','sandbox')


@pytest.fixture
def db():
    engine = create_engine('sqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine,tables=TABLES)
    with sessionmaker(bind=engine)() as session: yield session
    engine.dispose()


def seed(db):
    from test_fiscal_rules import draft
    invoice = draft(payment_method='PPD',payment_form='99')
    invoice.id='credit-invoice'; invoice.company_id=CTX.tenant_id
    invoice.items[0].company_id=CTX.tenant_id
    root = ET.fromstring(commercial._build_cfdi_xml(invoice))
    root.set('Fecha',(datetime.now(timezone.utc)-timedelta(days=3)).astimezone(svc.LOCAL).strftime('%Y-%m-%dT%H:%M:%S'))
    ET.SubElement(ET.SubElement(root,'{'+svc.CFDI+'}Complemento'),'{http://www.sat.gob.mx/TimbreFiscalDigital}TimbreFiscalDigital',{'UUID':'11111111-2222-4333-8444-555555555555'})
    invoice.stamped_xml_base64=base64.b64encode(ET.tostring(root)).decode()
    invoice.stamp_status='stamped'; invoice.status='stamped'; invoice.stamped_at=datetime.utcnow()
    db.add(invoice); db.commit()
    return invoice


def body(amount='58.00', reference=None, **changes):
    return RegisterPayment(**{'amount':amount,'reference':reference or str(uuid4()),'idempotency_key':str(uuid4()),
        'paid_at':datetime.now(timezone.utc)-timedelta(hours=1),'payment_form':'03',**changes})


def record(db, payload=None): return api.register_payment('credit-invoice',payload or body(),CTX,db)
def stamp(db,payment_id): return api.stamp_receipt('credit-invoice',payment_id,CTX,db)


def accepted(**kwargs):
    root=ET.fromstring(base64.b64decode(kwargs['xml_base64']))
    ET.SubElement(root.find('c:Complemento',svc.NS),'{http://www.sat.gob.mx/TimbreFiscalDigital}TimbreFiscalDigital',{'UUID':str(uuid4())})
    return {'ok':True,'status_code':200,'provider_response':{'Codigo':0,'Xml':ET.tostring(root,encoding='unicode')}}


def test_partial_then_final_balance_and_rep_structure(db,monkeypatch):
    seed(db); pac=Mock(side_effect=accepted); monkeypatch.setattr(api.pac,'timbra_cfdi',pac)
    first=record(db,body('58.00','bank-001'))
    summary=api.list_payments('credit-invoice',CTX,db)
    assert summary['balance']=='58.00' and summary['paid']=='58.00'
    assert summary['payments'][0]['bank_verification']=='not_performed'
    prepared=api.prepare_receipt('credit-invoice',first['id'],CTX,db)
    pac.assert_not_called(); assert prepared['preliminary'] is True
    root=ET.fromstring(base64.b64decode(prepared['xml_base64']))
    assert root.get('TipoDeComprobante')=='P' and root.get('Moneda')=='XXX' and root.get('Total')=='0'
    assert 'MetodoPago' not in root.attrib and 'FormaPago' not in root.attrib
    assert root.find('c:Receptor',svc.NS).get('UsoCFDI')=='CP01'
    doc=root.find('c:Complemento/p:Pagos/p:Pago/p:DoctoRelacionado',svc.NS)
    assert doc.get('NumParcialidad')=='1' and doc.get('ImpSaldoAnt')=='116.00' and doc.get('ImpSaldoInsoluto')=='58.00'
    tax=doc.find('p:ImpuestosDR/p:TrasladosDR/p:TrasladoDR',svc.NS)
    assert tax.get('BaseDR')=='50.000000' and tax.get('ImporteDR')=='8.000000'
    assert stamp(db,first['id'])['ok'] is True
    second=record(db,body('58.00','bank-002')); assert stamp(db,second['id'])['ok'] is True
    assert api.list_payments('credit-invoice',CTX,db)['balance']=='0.00'
    with pytest.raises(HTTPException): record(db,body('0.01'))
    with pytest.raises(HTTPException): stamp(db,first['id'])
    assert pac.call_count==2
    assert db.query(PaymentAct).count()==8
    original=db.query(BillingDraft).filter_by(id='credit-invoice').one()
    assert svc.invoice_snapshot(original)['total']=='116.00'


def test_idempotent_retries_and_reference_reuse(db):
    seed(db); request=body('50.00','  BANK  001  ')
    a=record(db,request); b=record(db,request); assert a['id']==b['id']
    with pytest.raises(HTTPException) as exc: record(db,body('10.00','BANK 001'))
    assert exc.value.status_code==409
    altered=request.model_copy(update={'amount':Decimal('10')})
    with pytest.raises(HTTPException) as exc: record(db,altered)
    assert exc.value.status_code==409 and db.query(BillingPayment).count()==1


@pytest.mark.parametrize('changes',[
    {'amount':'117.00'}, {'paid_at':datetime.now(timezone.utc)+timedelta(days=1)},
    {'paid_at':datetime.now(timezone.utc)-timedelta(days=30)},
])
def test_invalid_collections_do_not_write(db,changes):
    seed(db)
    with pytest.raises(HTTPException): record(db,body(**changes))
    assert db.query(BillingPayment).count()==0


@pytest.mark.parametrize('changes',[{'amount':'0'},{'amount':'-1'},{'amount':'1.001'},
    {'amount':'NaN'},{'amount':'1e100'},{'paid_at':'2026-10-20T12:00:00'}, {'payment_form':'99'}, {'reference':'   '}, {'extra':'injected'}])
def test_strict_payment_input(changes):
    with pytest.raises(ValidationError): body(**changes)


def test_tenant_and_feature_boundaries(db,monkeypatch):
    seed(db)
    from app.core.auth import SecurityContext
    other=SecurityContext('foreign','outsider','operator')
    with pytest.raises(HTTPException) as exc: api.list_payments('credit-invoice',other,db)
    assert exc.value.status_code==404
    monkeypatch.setattr(api.settings,'FISCAL_PPD_ENABLED',False)
    with pytest.raises(HTTPException) as exc: record(db)
    assert exc.value.status_code==503
    monkeypatch.setattr(api.settings,'FISCAL_PPD_ENABLED',True); monkeypatch.setattr(api.settings,'TIMBRACFDI_ENV','production')
    with pytest.raises(HTTPException) as exc: record(db)
    assert exc.value.status_code==503


def test_unknown_result_is_not_reissued_or_collected_twice(db,monkeypatch):
    seed(db); item=record(db)
    pac=Mock(side_effect=TimeoutError()); monkeypatch.setattr(api.pac,'timbra_cfdi',pac)
    with pytest.raises(HTTPException) as exc: stamp(db,item['id'])
    assert exc.value.status_code==502
    assert api.read_receipt('credit-invoice',item['id'],CTX,db)['receipt_status']=='unknown'
    with pytest.raises(HTTPException) as exc: stamp(db,item['id'])
    assert exc.value.status_code==409 and pac.call_count==1
    assert api.list_payments('credit-invoice',CTX,db)['balance']=='58.00'


def test_rejection_allows_same_receipt_retry_without_new_payment(db,monkeypatch):
    seed(db); item=record(db)
    pac=Mock(return_value={'ok':True,'status_code':200,'provider_response':{'Codigo':301,'Mensaje':'Synthetic rejection'}})
    monkeypatch.setattr(api.pac,'timbra_cfdi',pac)
    assert stamp(db,item['id'])['receipt_status']=='rejected'
    pac.side_effect=accepted
    assert stamp(db,item['id'])['ok']
    assert db.query(BillingPayment).count()==1 and db.query(PaymentAct).count()==7


def test_wrong_returned_xml_is_unknown(db,monkeypatch):
    seed(db); item=record(db)
    def wrong(**kwargs):
        value=accepted(**kwargs)
        value['provider_response']['Xml']=value['provider_response']['Xml'].replace('ImpPagado="58.00"','ImpPagado="1.00"')
        return value
    monkeypatch.setattr(api.pac,'timbra_cfdi',Mock(side_effect=wrong))
    with pytest.raises(HTTPException): stamp(db,item['id'])
    assert api.read_receipt('credit-invoice',item['id'],CTX,db)['receipt_status']=='unknown'


def test_later_receipt_waits_for_prior_reconciliation(db,monkeypatch):
    seed(db); record(db,body('50')); second=record(db,body('66'))
    pac=Mock();monkeypatch.setattr(api.pac,'timbra_cfdi',pac)
    with pytest.raises(HTTPException): stamp(db,second['id'])
    pac.assert_not_called()


def test_failed_audit_before_provider_rolls_back_reservation(db,monkeypatch):
    seed(db); item=record(db); pac=Mock(); monkeypatch.setattr(api.pac,'timbra_cfdi',pac)
    monkeypatch.setattr(svc,'append_act',Mock(side_effect=RuntimeError('synthetic journal failure')))
    with pytest.raises(RuntimeError): stamp(db,item['id'])
    db.rollback(); pac.assert_not_called()
    assert api.read_receipt('credit-invoice',item['id'],CTX,db)['receipt_status']=='not_sent'


def test_failed_result_audit_preserves_processing_and_request(db,monkeypatch):
    seed(db); item=record(db); original=svc.append_act
    def append(*args,**kwargs):
        if args[4]=='confirmed': raise RuntimeError('synthetic result write failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(svc,'append_act',append); monkeypatch.setattr(api.pac,'timbra_cfdi',Mock(side_effect=accepted))
    with pytest.raises(RuntimeError): stamp(db,item['id'])
    db.rollback()
    assert api.read_receipt('credit-invoice',item['id'],CTX,db)['receipt_status']=='processing'
    assert db.query(PaymentReceipt).one().request_xml
    with pytest.raises(HTTPException): stamp(db,item['id'])


def test_payment_ledger_tamper_prevents_provider(db,monkeypatch):
    seed(db); item=record(db)
    db.execute(text("UPDATE billing_payments SET declaration='{}'"));db.commit();db.expire_all()
    pac=Mock();monkeypatch.setattr(api.pac,'timbra_cfdi',pac)
    with pytest.raises(HTTPException) as exc: stamp(db,item['id'])
    assert exc.value.status_code==409;pac.assert_not_called()


def test_ppd_form_99_and_pue_99_rules(db):
    from test_fiscal_rules import draft
    valid=draft(payment_method='PPD',payment_form='99')
    assert commercial._serialize_draft(valid).ready_to_stamp
    root=ET.fromstring(commercial._build_cfdi_xml(valid)); assert root.get('FormaPago')=='99'
    assert not commercial._serialize_draft(draft(payment_method='PPD',payment_form='03')).ready_to_stamp
    assert not commercial._serialize_draft(draft(payment_method='PUE',payment_form='99')).ready_to_stamp


def test_no_implicit_emitter_when_request_omits_it(db):
    value=commercial.create_billing_draft(commercial.BillingDraftCreateRequest(customer_rfc='AAA010101AAA'),CTX,db)
    assert value.emitter_rfc=='' and not value.ready_to_stamp


def test_postgres_simultaneous_overpayment_serializes(pg_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    Base.metadata.create_all(pg_engine,tables=[BillingPayment.__table__,PaymentReceipt.__table__,PaymentAct.__table__])
    with sessionmaker(bind=pg_engine)() as db: seed(db)
    barrier=Barrier(2)
    def attempt(number):
        with sessionmaker(bind=pg_engine)() as db:
            barrier.wait(timeout=10)
            try:
                return api.register_payment('credit-invoice',body('80',f'BANK-{number}'),CTX,db)['balance_after']
            except HTTPException as exc:
                db.rollback();return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool: outcomes=list(pool.map(attempt,[1,2]))
    assert sorted(map(str,outcomes))==['36.00','409']
    with sessionmaker(bind=pg_engine)() as db:
        assert db.query(BillingPayment).count()==1


def test_postgres_payment_migration_and_evidence_guards(pg_engine):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError
    path=Path(__file__).resolve().parents[1]/'migrations/versions/20261025_02_payment_cycle.py'
    spec=importlib.util.spec_from_file_location('payment_migration',path); module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pg_engine.begin() as conn:
        module.op=Operations(MigrationContext.configure(conn));module.upgrade();module.upgrade()
    with sessionmaker(bind=pg_engine)() as db:
        seed(db); record(db)
    for table in ('billing_payments','billing_payment_acts'):
        for statement in (f'UPDATE {table} SET company_id=company_id',f'DELETE FROM {table}',f'TRUNCATE {table} CASCADE'):
            with pytest.raises(DBAPIError,match='append-only'):
                with pg_engine.begin() as conn: conn.execute(text(statement))
    with pytest.raises(DBAPIError,match='foreign key'):
        with pg_engine.begin() as conn:
            conn.execute(text("INSERT INTO billing_payment_receipts (payment_id,company_id,status) VALUES ('foreign','other','not_sent')"))
    with pg_engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM billing_payment_acts'))==1


def test_fractional_collections_conserve_total_base_and_tax(db):
    seed(db)
    for amount in ('.01','.02','13.77','70.19','32.01'): record(db,body(amount))
    rows=db.query(BillingPayment).order_by(BillingPayment.sequence).all()
    assert sum(row.amount for row in rows)==Decimal('116') and rows[-1].balance_after==0
    assert sum(row.tax_base for row in rows)==Decimal('100')
    assert sum(row.tax_amount for row in rows)==Decimal('16')
    for row in rows: assert row.balance_before-row.amount==row.balance_after


def test_http_jwt_roles_tenants_and_payload_guards(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db.session import get_db
    from app.services.jwt_service import create_access_token
    seed(db); app=FastAPI(); app.include_router(api.router)
    def override(): yield db
    app.dependency_overrides[get_db]=override
    client=TestClient(app)
    path='/v1/billing/drafts/credit-invoice/payments'
    def headers(role='operator',tenant=CTX.tenant_id):
        return {'Authorization':'Bearer '+create_access_token({'sub':'synthetic','tenant':tenant,'role':role})}
    assert client.get(path).status_code==401
    assert client.post(path,headers=headers('viewer'),json=body().model_dump(mode='json')).status_code==403
    assert client.get(path,headers=headers(tenant='foreign')).status_code==404
    assert client.post(path,headers=headers(),json={'amount':-1}).status_code==422
    response=client.post(path+'?company_id=foreign',headers=headers(),json=body().model_dump(mode='json'))
    assert response.status_code==201
    assert db.query(BillingPayment).one().company_id==CTX.tenant_id
    assert client.get(path,headers=headers('viewer')).status_code==200


def test_postgres_simultaneous_same_payment_is_idempotent(pg_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    Base.metadata.create_all(pg_engine,tables=[BillingPayment.__table__,PaymentReceipt.__table__,PaymentAct.__table__])
    with sessionmaker(bind=pg_engine)() as db: seed(db)
    barrier=Barrier(2); request=body('58','BANK-SAME')
    def attempt(_):
        with sessionmaker(bind=pg_engine)() as db:
            barrier.wait(timeout=10)
            return api.register_payment('credit-invoice',request,CTX,db)['id']
    with ThreadPoolExecutor(max_workers=2) as pool: ids=list(pool.map(attempt,[1,2]))
    assert ids[0]==ids[1]
    with sessionmaker(bind=pg_engine)() as db: assert db.query(BillingPayment).count()==1


def test_postgres_two_receipt_attempts_only_call_provider_once(pg_engine,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    Base.metadata.create_all(pg_engine,tables=[BillingPayment.__table__,PaymentReceipt.__table__,PaymentAct.__table__])
    with sessionmaker(bind=pg_engine)() as db:
        seed(db); item=record(db)
    entered,release=Event(),Event()
    def provider(**kwargs):
        entered.set(); assert release.wait(timeout=10); return accepted(**kwargs)
    pac=Mock(side_effect=provider);monkeypatch.setattr(api.pac,'timbra_cfdi',pac)
    def attempt():
        with sessionmaker(bind=pg_engine)() as db: return stamp(db,item['id'])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(attempt)
        try:
            assert entered.wait(timeout=10)
            with sessionmaker(bind=pg_engine)() as db:
                with pytest.raises(HTTPException) as exc: stamp(db,item['id'])
                assert exc.value.status_code==409
        finally: release.set()
        assert future.result(timeout=10)['ok']
    assert pac.call_count==1
