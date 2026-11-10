"""Known XML only, independent observations; external HTTP and fiscal writes forbidden."""
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from app.models.audit_log import AuditLog
from app.models.billing_cancellation import BillingCancellation
from app.services import sat_tracking as svc, audit_service, billing_cancellations as cancellations
from app.api.routers import sat_tracking_v1 as api
from test_payment_cycle import db, seed, CTX, no_network
from test_billing_stamp_integration import pg_engine

FOLIO='11111111-2222-4333-8444-555555555555'

@pytest.fixture
def prepared(db,monkeypatch):
    AuditLog.__table__.create(db.bind)
    draft=seed(db)
    monkeypatch.setattr(api.settings,'TIMBRACFDI_ENV','production')
    monkeypatch.setattr(api.settings,'FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC',draft.emitter_rfc)
    for name in ('cancel_at_pac',): monkeypatch.setattr(svc.production.live,name,Mock(side_effect=AssertionError('Fiscal write forbidden')))
    yield draft


def answer(state='Vigente'):
    return {'Estado':state,'CodigoEstatus':'S - Comprobante obtenido satisfactoriamente.','EsCancelable':'Cancelable sin aceptación'}


def test_get_never_contacts_sat_and_post_only_records_observations(db,prepared,monkeypatch):
    query=Mock(return_value=answer('Cancelado'));monkeypatch.setattr(svc.production.live,'sat_status',query)
    original=(prepared.stamp_status,prepared.stamped_xml_base64)
    assert api.read(prepared.id,None,UUID(FOLIO),CTX,db)['history']==[];query.assert_not_called()
    result=api.consult(prepared.id,None,UUID(FOLIO),CTX,db)
    assert result['last_answer']['response']['Estado']=='Cancelado'
    assert result['last_answer']['source']==svc.SOURCE
    assert result['last_answer']['checked_at']
    assert (prepared.stamp_status,prepared.stamped_xml_base64)==original
    assert db.query(BillingCancellation).count()==0
    assert db.query(AuditLog).count()==1
    assert api.consult(prepared.id,None,UUID(FOLIO),CTX,db)['cached'] is True
    assert query.call_count==1


def test_failed_consult_preserves_prior_answer_and_both_history_entries(db,prepared,monkeypatch):
    moment=[datetime.now(timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return moment[0]
    monkeypatch.setattr(svc,'datetime',Clock)
    query=Mock(side_effect=[answer(),RuntimeError('secret token must not escape')]);monkeypatch.setattr(svc.production.live,'sat_status',query)
    api.consult(prepared.id,None,UUID(FOLIO),CTX,db)
    moment[0]+=timedelta(minutes=1)
    result=api.consult(prepared.id,None,UUID(FOLIO),CTX,db)
    assert result['latest_attempt']['outcome']=='query_failed'
    assert result['last_answer']['response']['Estado']=='Vigente'
    assert len(result['history'])==2
    assert 'secret token' not in json.dumps(result)


def test_recovers_preserved_cancellation_evidence_without_a_query(db,prepared,monkeypatch):
    root,folio,content_hash=svc.document(db,prepared,None,FOLIO)
    row=BillingCancellation(id='cancel',company_id=prepared.company_id,draft_id=prepared.id,document_uuid=folio,
        document_hash=content_hash,idempotency_key='operation',status='cancelled',intent=json.dumps({'reason':'03','replacement_uuid':None}))
    db.add(row);db.flush()
    cancellations.append_act(db,row,CTX,'status_consulted','cancelled',{'status_response':answer('Cancelado')});db.commit()
    query=Mock(side_effect=AssertionError('GET must not query'));monkeypatch.setattr(svc.production.live,'sat_status',query)
    result=api.read(prepared.id,None,UUID(FOLIO),CTX,db)
    assert result['last_answer']['response']['Estado']=='Cancelado'
    assert result['last_answer']['origin']=='cancellation_history'
    assert result['last_answer']['checked_at']
    query.assert_not_called()


@pytest.mark.parametrize('case',['tenant','uuid','emitter','sandbox'])
def test_scope_blocks_before_network(db,prepared,monkeypatch,case):
    from app.core.auth import SecurityContext
    ctx=CTX;uuid=UUID(FOLIO)
    if case=='tenant': ctx=SecurityContext('other','actor','viewer')
    if case=='uuid': uuid=UUID('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
    if case=='emitter':monkeypatch.setattr(api.settings,'FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC','OTHER')
    if case=='sandbox':monkeypatch.setattr(api.settings,'TIMBRACFDI_ENV','sandbox')
    query=Mock(side_effect=AssertionError('Network forbidden'));monkeypatch.setattr(svc.production.live,'sat_status',query)
    with pytest.raises(HTTPException):api.consult(prepared.id,None,uuid,ctx,db)
    query.assert_not_called();assert db.query(AuditLog).count()==0


@pytest.mark.parametrize('code,state,valid', [('N - 602: Comprobante no encontrado.','No Encontrado',True),('N - 601: expresión inválida','No Encontrado',False)])
def test_not_found_is_distinct_from_failed_query_and_old_cancel_gate_stays_strict(db,prepared,monkeypatch,code,state,valid):
    root,_,_=svc.document(db,prepared,None,FOLIO)
    response=Mock(status_code=200,text=f'<Respuesta><CodigoEstatus>{code}</CodigoEstatus><Estado>{state}</Estado></Respuesta>')
    monkeypatch.setattr(svc.production.live.requests,'post',Mock(return_value=response))
    if valid: assert svc.production.live.sat_status(root,FOLIO,allow_not_found=True)['Estado']=='No localizado'
    else:
        with pytest.raises(HTTPException):svc.production.live.sat_status(root,FOLIO,allow_not_found=True)
    with pytest.raises(HTTPException):svc.production.live.sat_status(root,FOLIO)


def test_receipt_query_is_bound_to_its_payment_and_preserves_money(db,prepared,monkeypatch):
    import base64
    from xml.etree import ElementTree as ET
    from test_payment_cycle import record
    from app.models.billing_payment import PaymentReceipt, BillingPayment
    monkeypatch.setattr(api.settings,'TIMBRACFDI_ENV','sandbox')
    payment=record(db)
    receipt=db.query(PaymentReceipt).filter_by(payment_id=payment['id']).one()
    root=ET.fromstring(base64.b64decode(prepared.stamped_xml_base64));root.set('TipoDeComprobante','P')
    folio='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
    root.find('.//{http://www.sat.gob.mx/TimbreFiscalDigital}TimbreFiscalDigital').set('UUID',folio)
    receipt.status='stamped';receipt.uuid=folio;receipt.xml_base64=base64.b64encode(ET.tostring(root)).decode();db.commit()
    before=db.query(BillingPayment).one().amount
    monkeypatch.setattr(api.settings,'TIMBRACFDI_ENV','production')
    monkeypatch.setattr(svc.production.live,'sat_status',Mock(return_value=answer()))
    result=api.consult(prepared.id,payment['id'],UUID(folio),CTX,db)
    assert result['document_uuid']==folio
    assert db.query(BillingPayment).one().amount==before
    assert api.read(prepared.id,None,UUID(FOLIO),CTX,db)['history']==[]


def test_concurrent_audit_writers_keep_a_single_tenant_chain(pg_engine):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.orm import Session
    AuditLog.__table__.create(pg_engine)
    def write(index):
        with Session(pg_engine) as session:
            audit_service.create_audit_log(session,'sat-tenant','actor','sat_status_consulted','sat_document',str(index),details={'synthetic':True})
    with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(write,range(8)))
    with Session(pg_engine) as session:
        assert session.query(AuditLog).count()==8
        assert audit_service.verify_audit_chain(session,'sat-tenant')==(True,None)


def test_last_answer_survives_more_than_twenty_failed_observations(db,prepared,monkeypatch):
    moment=[datetime.now(timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return moment[0]
    monkeypatch.setattr(svc,'datetime',Clock)
    query=Mock(return_value=answer());monkeypatch.setattr(svc.production.live,'sat_status',query)
    api.consult(prepared.id,None,UUID(FOLIO),CTX,db)
    query.side_effect=RuntimeError('offline')
    for _ in range(21):
        moment[0]+=timedelta(minutes=1)
        result=api.consult(prepared.id,None,UUID(FOLIO),CTX,db)
    assert result['last_answer']['response']['Estado']=='Vigente'
    assert len(result['history'])==20
    assert all(row['outcome']=='query_failed' for row in result['history'])
