"""All external outcomes are controlled; never issues or cancels a real CFDI."""
import base64
from uuid import uuid4
from unittest.mock import Mock
from xml.etree import ElementTree as ET
import pytest
from fastapi import HTTPException
from app.core.auth import SecurityContext
from app.core.config import settings
from app.api.routers import cancellations_v1 as cancel, fiscal_revisions_v1 as correction, payments_v1 as payments
from app.services import production_cancellation as live, billing_cancellations as svc
from app.models.billing_cancellation import BillingCancellation
from app.models.billing_payment import BillingPayment
from test_payment_cycle import db, CTX as BASE_CTX, body
from test_billing_cancellations import cancel_body as sandbox_cancel_body
from test_fiscal_revisions import seed_valid, request as revision_body, accepted

def cancel_body(db,invoice,payment_id=None,**changes):
    return sandbox_cancel_body(expected_uuid=svc.target(db,invoice,payment_id)[1],**changes)


def act(db,invoice,operation,payload=None):
    from app.models.fiscal_revision import FiscalRevision
    from app.schemas.fiscal_revision import RevisionOperation
    from app.schemas.billing_cancellation import RecoverReceipt
    if operation not in {'create','read'}:
        revision=db.query(FiscalRevision).filter_by(draft_id=invoice.id).one()
        payload=RecoverReceipt(receipt_uuid=payload.receipt_uuid,revision_id=revision.id) if operation=='reconcile' else RevisionOperation(revision_id=revision.id)
    return correction.action(invoice.id,operation,payload,CTX,db)

CTX=SecurityContext(BASE_CTX.tenant_id,'synthetic-admin','admin')
VIGENTE={'CodigoEstatus':'S - Synthetic','Estado':'Vigente','EsCancelable':'Cancelable con aceptación','EstatusCancelacion':''}
CANCELLED={**VIGENTE,'Estado':'Cancelado','EstatusCancelacion':'Cancelado con aceptación'}

@pytest.fixture(autouse=True)
def production(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request',Mock(side_effect=AssertionError('External network forbidden')))
    for key,value in {'TIMBRACFDI_ENV':'production','TIMBRACFDI_USE_SOAP':True,
        'TIMBRACFDI_SOAP_URL':'https://timbracfdi33.mx:1443/Timbrado.asmx','TIMBRACFDI_TOKEN':'synthetic',
        'FISCAL_PPD_ENABLED':True,'FISCAL_PPD_PRODUCTION_ENABLED':True,'FISCAL_REP_STAMP_ENABLED':True,
        'FISCAL_PPD_PRODUCTION_EMITTER_RFC':'EKU9003173C9',
        'FISCAL_CANCELLATION_ENABLED':True,'FISCAL_CORRECTIONS_ENABLED':True,
        'FISCAL_CANCELLATION_PRODUCTION_ENABLED':True,'FISCAL_CORRECTIONS_PRODUCTION_ENABLED':True,
        'FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC':'EKU9003173C9','LIVE_CANCEL_ENABLED':False}.items():
        monkeypatch.setattr(settings,key,value)
    monkeypatch.setattr(live,'sat_status',Mock(return_value=VIGENTE))


def wire(monkeypatch):
    def response(emitter,folio,*_):
        return 200,f'<Acuse RfcEmisor="{emitter}"><Folios><UUID>{folio}</UUID><EstatusUUID>201</EstatusUUID></Folios></Acuse>'
    pac=Mock(side_effect=response);monkeypatch.setattr(live,'cancel_at_pac',pac);return pac


def test_ack_with_acceptance_waits_and_repeated_click_never_resends(db,monkeypatch):
    invoice=seed_valid(db); original=invoice.stamped_xml_base64;pac=wire(monkeypatch);payload=cancel_body(db,invoice)
    first=cancel.cancel_invoice(invoice.id,payload,CTX,db)
    assert first['status']=='requested' and first['environment']=='production'
    assert cancel.cancel_invoice(invoice.id,payload,CTX,db)['status']=='requested' and pac.call_count==1
    live.sat_status.return_value={**VIGENTE,'EstatusCancelacion':'En proceso'}
    assert cancel.reconcile_invoice(invoice.id,CTX,db)['status']=='requested'
    live.sat_status.return_value=CANCELLED
    assert cancel.reconcile_invoice(invoice.id,CTX,db)['status']=='cancelled'
    assert invoice.stamped_xml_base64==original and invoice.stamp_status=='stamped'
    assert db.query(BillingPayment).count()==0 and pac.call_count==1


def test_response_lost_and_rejection_do_not_resubmit(db,monkeypatch):
    invoice=seed_valid(db);pac=Mock(side_effect=TimeoutError());monkeypatch.setattr(live,'cancel_at_pac',pac);payload=cancel_body(db,invoice)
    assert cancel.cancel_invoice(invoice.id,payload,CTX,db)['status']=='unknown'
    assert cancel.cancel_invoice(invoice.id,payload,CTX,db)['status']=='unknown' and pac.call_count==1
    live.sat_status.return_value={**VIGENTE,'EstatusCancelacion':'Solicitud rechazada'}
    assert cancel.reconcile_invoice(invoice.id,CTX,db)['status']=='rejected'
    assert cancel.cancel_invoice(invoice.id,payload,CTX,db)['status']=='rejected' and pac.call_count==1

@pytest.mark.parametrize('state',[{**VIGENTE,'EsCancelable':'No cancelable'},CANCELLED])
def test_ineligible_sat_result_does_not_reserve_or_call_pac(db,monkeypatch,state):
    invoice=seed_valid(db);pac=wire(monkeypatch);live.sat_status.return_value=state
    with pytest.raises(HTTPException): cancel.cancel_invoice(invoice.id,cancel_body(db,invoice),CTX,db)
    assert db.query(BillingCancellation).count()==0;pac.assert_not_called()


def test_sat_timeout_has_no_reservation(db,monkeypatch):
    invoice=seed_valid(db);pac=wire(monkeypatch);live.sat_status.side_effect=TimeoutError()
    with pytest.raises(HTTPException) as exc: cancel.cancel_invoice(invoice.id,cancel_body(db,invoice),CTX,db)
    assert exc.value.status_code==502 and db.query(BillingCancellation).count()==0;pac.assert_not_called()

@pytest.mark.parametrize('ctx',[SecurityContext(BASE_CTX.tenant_id,'seller','operator'),SecurityContext('other','admin','admin')])
def test_role_and_tenant_do_not_reach_sat_or_pac(db,monkeypatch,ctx):
    invoice=seed_valid(db);pac=wire(monkeypatch)
    with pytest.raises(HTTPException): cancel.cancel_invoice(invoice.id,cancel_body(db,invoice),ctx,db)
    live.sat_status.assert_not_called();pac.assert_not_called()

@pytest.mark.parametrize('key,value',[('FISCAL_CANCELLATION_PRODUCTION_ENABLED',False),
    ('FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC','OTHER010101AAA'),
    ('TIMBRACFDI_SOAP_URL','https://foreign.test/Timbrado.asmx')])
def test_production_scope_fails_closed(db,monkeypatch,key,value):
    invoice=seed_valid(db);pac=wire(monkeypatch);monkeypatch.setattr(settings,key,value)
    with pytest.raises(HTTPException): cancel.cancel_invoice(invoice.id,cancel_body(db,invoice),CTX,db)
    pac.assert_not_called();live.sat_status.assert_not_called()


def test_substitution_pending_rejection_recovery_and_cancel_chain(db,monkeypatch):
    invoice=seed_valid(db);original=invoice.stamped_xml_base64;pac=wire(monkeypatch)
    prepared=act(db,invoice,'create',revision_body(db))
    assert prepared['environment']=='production' and prepared['pending']
    signer=Mock(side_effect=accepted);monkeypatch.setattr(correction.pac,'timbra_cfdi',signer)
    issued=act(db,invoice,'stamp',None)
    new_uuid=issued['history'][0]['result_uuid']
    assert issued['active_uuid']!=new_uuid
    requested=act(db,invoice,'cancel-original',None)
    assert requested['pending'] and pac.call_args.args[2:] == ('01',new_uuid)
    live.sat_status.return_value={**VIGENTE,'EstatusCancelacion':'En proceso'}
    assert act(db,invoice,'confirm-cancellation',None)['pending']
    live.sat_status.return_value=CANCELLED
    confirmed=act(db,invoice,'confirm-cancellation',None)
    assert not confirmed['pending'] and confirmed['active_uuid']==new_uuid
    assert invoice.stamped_xml_base64==original and db.query(BillingPayment).count()==0
    assert signer.call_count==1 and pac.call_count==1


def test_legacy_cancelled_pilot_cannot_be_cancelled_or_replaced_again(db,monkeypatch):
    invoice=seed_valid(db);root=ET.fromstring(base64.b64decode(invoice.stamped_xml_base64));root.set('MetodoPago','PUE');root.set('FormaPago','03')
    xml=ET.tostring(root,encoding='unicode');invoice.stamped_xml_base64=base64.b64encode(xml.encode()).decode();invoice.payment_method='PUE';invoice.payment_form='03';db.commit()
    folio=payments.pac.extract_stamp_uuid(xml).lower()
    for key,value in {'LIVE_CANCEL_ENABLED':True,'LIVE_CANCEL_DRAFT_ID':invoice.id,'LIVE_CANCEL_UUID':folio,'LIVE_CANCEL_XML_SHA256':live.digest(xml)}.items():monkeypatch.setattr(settings,key,value)
    live.install(db.bind);pac=wire(monkeypatch)
    live.sat_status.side_effect=[{**VIGENTE,'EsCancelable':'Cancelable sin aceptación'},CANCELLED]
    assert live.submit(db,invoice.id,CTX,folio)['status']=='cancelled'
    before=invoice.stamped_xml_base64
    monkeypatch.setattr(settings,'LIVE_CANCEL_ENABLED',False)
    assert cancel.read_invoice(invoice.id,CTX,db)['status']=='cancelled'
    assert cancel.cancel_invoice(invoice.id,cancel_body(db,invoice),CTX,db)['status']=='cancelled'
    with pytest.raises(HTTPException): act(db,invoice,'create',revision_body(db))
    assert invoice.stamped_xml_base64==before and pac.call_count==1 and db.query(BillingCancellation).count()==0


def test_rep_cancel_preserves_cash_and_respects_reverse_partiality(db,monkeypatch):
    invoice=seed_valid(db);pac=wire(monkeypatch);monkeypatch.setattr(payments.pac,'timbra_cfdi',Mock(side_effect=accepted))
    ids=[]
    for _ in range(2):
        item=payments.register_payment(invoice.id,body('58'),CTX,db)
        payments.stamp_receipt(invoice.id,item['id'],CTX,db);ids.append(item['id'])
    with pytest.raises(HTTPException): cancel.cancel_receipt(invoice.id,ids[0],cancel_body(db,invoice,ids[0]),CTX,db)
    pac.assert_not_called()
    assert cancel.cancel_receipt(invoice.id,ids[1],cancel_body(db,invoice,ids[1]),CTX,db)['status']=='requested'
    live.sat_status.return_value=CANCELLED
    assert cancel.reconcile_receipt(invoice.id,ids[1],CTX,db)['status']=='cancelled'
    assert db.query(BillingPayment).count()==2
    assert payments.list_payments(invoice.id,CTX,db)['balance']=='0.00'


def test_unknown_substitution_recovers_without_reissue(db,monkeypatch):
    from app.models.fiscal_revision import FiscalRevision
    from app.schemas.billing_cancellation import RecoverReceipt
    invoice=seed_valid(db);act(db,invoice,'create',revision_body(db))
    signer=Mock(side_effect=TimeoutError());monkeypatch.setattr(correction.pac,'timbra_cfdi',signer)
    assert act(db,invoice,'stamp',None)['history'][0]['status']=='unknown'
    with pytest.raises(HTTPException):act(db,invoice,'stamp',None)
    row=db.query(FiscalRevision).one();result=accepted(xml_base64=base64.b64encode(row.request_xml.encode()).decode())
    folio=payments.pac.extract_stamp_uuid(result['provider_response']['Xml'])
    retrieve=Mock(return_value=result);monkeypatch.setattr(correction.pac,'retrieve_cfdi',retrieve)
    recovered=act(db,invoice,'reconcile',RecoverReceipt(receipt_uuid=folio))
    assert recovered['history'][0]['result_uuid']==folio and signer.call_count==1 and retrieve.call_count==1

@pytest.mark.parametrize('key,value',[('AUTH_CONNECTOR','none'),('AUTH_ALLOW_ANONYMOUS',True)])
def test_production_requires_jwt_and_no_anonymous(db,monkeypatch,key,value):
    invoice=seed_valid(db);monkeypatch.setattr(settings,key,value)
    with pytest.raises(HTTPException): cancel.cancel_invoice(invoice.id,cancel_body(db,invoice),CTX,db)
    live.sat_status.assert_not_called()

from test_billing_stamp_integration import pg_engine

def test_production_concurrent_clicks_reserve_before_external_call(pg_engine,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy.orm import sessionmaker
    from scripts.install_fiscal_cycle import install
    install(pg_engine)
    with sessionmaker(bind=pg_engine)() as db:
        invoice=seed_valid(db);invoice_id=invoice.id;folio=svc.target(db,invoice)[1]
    entered,release=Event(),Event();payload=sandbox_cancel_body(expected_uuid=folio)
    def external(emitter,folio,*_):
        entered.set();assert release.wait(10)
        return 200,f'<Acuse RfcEmisor="{emitter}"><Folios><UUID>{folio}</UUID><EstatusUUID>201</EstatusUUID></Folios></Acuse>'
    pac=Mock(side_effect=external);monkeypatch.setattr(live,'cancel_at_pac',pac)
    def call():
        with sessionmaker(bind=pg_engine)() as db:return cancel.cancel_invoice(invoice_id,payload,CTX,db)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(call)
        try:
            assert entered.wait(10)
            assert call()['status']=='processing'
        finally:release.set()
        assert first.result(10)['status']=='requested'
    assert pac.call_count==1

@pytest.mark.parametrize('expected',[None,str(uuid4())])
def test_missing_or_stale_confirmation_never_calls_pac(db,monkeypatch,expected):
    invoice=seed_valid(db);pac=wire(monkeypatch)
    with pytest.raises(HTTPException) as exc: cancel.cancel_invoice(invoice.id,sandbox_cancel_body(expected_uuid=expected),CTX,db)
    assert exc.value.status_code==422 and db.query(BillingCancellation).count()==0
    pac.assert_not_called();live.sat_status.assert_not_called()


def test_http_revision_action_requires_exact_reviewed_revision(db,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db.session import get_db
    invoice=seed_valid(db);prepared=act(db,invoice,'create',revision_body(db));rid=prepared['history'][0]['id']
    pac=Mock(side_effect=accepted);monkeypatch.setattr(correction.pac,'timbra_cfdi',pac)
    app=FastAPI();app.include_router(correction.router)
    app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[correction.write_roles]=lambda:CTX
    with TestClient(app) as client:
        url=f'/v1/billing/drafts/{invoice.id}/correction/stamp'
        assert client.post(url,json={'revision_id':str(uuid4())}).status_code==422
        pac.assert_not_called()
        response=client.post(url,json={'revision_id':rid})
        assert response.status_code==200 and response.json()['history'][0]['status']=='stamped'
    assert pac.call_count==1
