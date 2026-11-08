import base64
import json
from uuid import uuid4
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import DatabaseError
from app.db.base import Base
from app.models import commercial, csf, evento, usuario, sucursal, user  # noqa
from app.models.commercial import BillingDraft, BillingDraftItem, ProductCatalog
from app.core.auth import SecurityContext
from app.services import production_cancellation as svc
from app.api.routers.production_cancellation_v1 import Confirmation

FOLIO = '11111111-2222-4333-8444-555555555555'
CTX = SecurityContext('tenant-a','actor-a','operator')
XML = f'''<c:Comprobante xmlns:c="http://www.sat.gob.mx/cfd/4" xmlns:t="http://www.sat.gob.mx/TimbreFiscalDigital" TipoDeComprobante="I" MetodoPago="PUE" Total="116.00" Sello="synthetic">
<c:Emisor Rfc="AAA010101AAA"/><c:Receptor Rfc="XAXX010101000"/><c:Complemento><t:TimbreFiscalDigital UUID="{FOLIO}"/></c:Complemento></c:Comprobante>'''

@pytest.fixture
def db(monkeypatch):
    engine=create_engine('sqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine,tables=[x.__table__ for x in (ProductCatalog,BillingDraft,BillingDraftItem)])
    svc.install(engine); svc.install(engine); svc.assert_schema(engine)
    with Session(engine) as session:
        session.add(BillingDraft(id='draft-a',company_id=CTX.tenant_id,stamp_status='stamped',
            emitter_rfc='AAA010101AAA',customer_rfc='XAXX010101000',payment_method='PUE',
            stamped_xml_base64=base64.b64encode(XML.encode()).decode()))
        session.commit()
        for key,value in dict(LIVE_CANCEL_ENABLED=True,LIVE_CANCEL_DRAFT_ID='draft-a',LIVE_CANCEL_UUID=FOLIO,
                LIVE_CANCEL_XML_SHA256=svc.digest(XML),TIMBRACFDI_ENV='production',
                TIMBRACFDI_SOAP_URL='https://timbracfdi33.mx:1443/Timbrado.asmx',
                TIMBRACFDI_PRODUCTION_TOKEN='synthetic-only').items(): monkeypatch.setattr(svc.settings,key,value)
        monkeypatch.setattr('requests.sessions.Session.request',Mock(side_effect=AssertionError('Network forbidden')))
        yield session
    engine.dispose()

def status(state='Vigente'):
    return {'Estado':state,'EsCancelable':'Cancelable sin aceptación','CodigoEstatus':'S - Comprobante obtenido satisfactoriamente.'}

def ack(folio=FOLIO):
    return f'<Acuse RfcEmisor="AAA010101AAA"><Folios><UUID>{folio}</UUID><EstatusUUID>201</EstatusUUID></Folios></Acuse>'

def provider(monkeypatch, states=None, response=None):
    sat=Mock(side_effect=states or [status(),status()]); pac=Mock(return_value=(200,response or ack()))
    monkeypatch.setattr(svc,'sat_status',sat); monkeypatch.setattr(svc,'cancel_at_pac',pac)
    return pac,sat

def test_ack_is_not_cancellation_then_confirmed_state_preserves_original(db,monkeypatch):
    pac,sat=provider(monkeypatch,[status(),status(),status('Cancelado')])
    original=db.query(BillingDraft).one().stamped_xml_base64
    result=svc.submit(db,'draft-a',CTX,FOLIO)
    assert result['status']=='requested' and result['sat_response']['Estado']=='Vigente'
    assert svc.submit(db,'draft-a',CTX,FOLIO)['status']=='requested' and pac.call_count==1
    assert svc.reconcile(db,'draft-a',CTX)['status']=='cancelled'
    assert svc.read(db,'draft-a',CTX)['status']=='cancelled'
    assert db.query(BillingDraft).one().stamped_xml_base64==original
    assert db.query(BillingDraft).one().stamp_status=='stamped'

def test_uncertain_provider_outcome_never_retries(db,monkeypatch):
    pac,sat=provider(monkeypatch,[status(),status(),status()])
    pac.side_effect=svc.requests.Timeout('Synthetic lost response')
    assert svc.submit(db,'draft-a',CTX,FOLIO)['status']=='unknown'
    assert svc.submit(db,'draft-a',CTX,FOLIO)['status']=='unknown'
    assert svc.reconcile(db,'draft-a',CTX)['status']=='unknown' and pac.call_count==1

@pytest.mark.parametrize('change',[{'LIVE_CANCEL_ENABLED':False},{'TIMBRACFDI_ENV':'sandbox'},
    {'TIMBRACFDI_ENV':'invalid'},{'LIVE_CANCEL_XML_SHA256':'wrong'},
    {'TIMBRACFDI_SOAP_URL':'https://pruebas.timbracfdi33.mx:1443/Timbrado.asmx'},
    {'TIMBRACFDI_SOAP_URL':'https://timbracfdi33.mx:1443/Timbrado.asmx?redirect=x'},
    {'TIMBRACFDI_SOAP_URL':'https://attacker.invalid/Timbrado.asmx'}])
def test_transport_and_pinned_document_fail_closed(db,monkeypatch,change):
    pac,sat=provider(monkeypatch)
    for k,v in change.items():monkeypatch.setattr(svc.settings,k,v)
    with pytest.raises(HTTPException):svc.submit(db,'draft-a',CTX,FOLIO)
    pac.assert_not_called();sat.assert_not_called()

def test_tenant_id_scope_and_confirmation(db,monkeypatch):
    pac,sat=provider(monkeypatch)
    for ctx,draft,folio in [(SecurityContext('tenant-b','b','operator'),'draft-a',FOLIO),(CTX,'other',FOLIO),(CTX,'draft-a',str(uuid4()))]:
        with pytest.raises(HTTPException):svc.submit(db,draft,ctx,folio)
    pac.assert_not_called();sat.assert_not_called()

def test_sat_not_cancelable_does_not_reserve_or_send(db,monkeypatch):
    pac,sat=provider(monkeypatch,[{**status(),'EsCancelable':'No cancelable'}])
    with pytest.raises(HTTPException):svc.submit(db,'draft-a',CTX,FOLIO)
    pac.assert_not_called();assert db.execute(svc.select(svc.intents)).first() is None

def test_missing_or_foreign_ack_is_uncertain(db,monkeypatch):
    provider(monkeypatch,response=ack(str(uuid4())))
    result=svc.submit(db,'draft-a',CTX,FOLIO)
    assert result['status']=='unknown' and result['acknowledgement_xml'] is None

def test_sql_audit_is_immutable_and_tampered_state_blocks(db,monkeypatch):
    provider(monkeypatch);svc.submit(db,'draft-a',CTX,FOLIO)
    with pytest.raises(DatabaseError):db.execute(text('DELETE FROM billing_live_cancel_events'))
    db.rollback()
    db.execute(update(svc.intents).values(state='cancelled'));db.commit()
    with pytest.raises(HTTPException):svc.read(db,'draft-a',CTX)

def test_current_wsdl_names_escaped_values_and_no_redirect(db,monkeypatch):
    captured=[]
    def post(url,**kwargs):
        captured.append((url,kwargs));return Mock(status_code=200,text='<response/>')
    monkeypatch.setattr(svc.requests,'post',post)
    svc.cancel_at_pac('AAA010101AAA',FOLIO)
    root=svc.E.fromstring(captured[0][1]['data']);fields={n.tag.rsplit('}',1)[-1]:n.text for n in root.iter()}
    assert fields['motivoCancelacion']=='03' and 'folioUUIDSustitucion' in fields
    assert fields['folioUUID']==FOLIO and captured[0][1]['allow_redirects'] is False
    assert captured[0][0]=='https://timbracfdi33.mx:1443/Timbrado.asmx'

def test_confirmation_is_strict():
    from pydantic import ValidationError
    for body in ({'confirm':False,'expected_uuid':FOLIO},{'confirm':True,'expected_uuid':FOLIO,'reason':'02'}):
        with pytest.raises(ValidationError):Confirmation(**body)

def test_two_connections_send_once_and_pg_audit_is_immutable(db,monkeypatch):
    import os
    from urllib.parse import urlparse
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock
    url=os.environ.get('LIVE_CANCEL_TEST_DATABASE_URL')
    if not url:pytest.skip('Explicit isolated PostgreSQL required')
    parsed=urlparse(url.replace('postgresql+psycopg:','postgresql:'))
    assert parsed.hostname in ('127.0.0.1','localhost') and parsed.path=='/digestor_fiscal_test'
    schema='live_cancel_'+uuid4().hex
    admin=create_engine(url)
    with admin.begin() as connection:connection.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:
        Base.metadata.create_all(engine,tables=[x.__table__ for x in (ProductCatalog,BillingDraft,BillingDraftItem)])
        svc.install(engine);svc.install(engine)
        with Session(engine) as session:
            session.add(BillingDraft(id='draft-a',company_id=CTX.tenant_id,stamp_status='stamped',
                emitter_rfc='AAA010101AAA',customer_rfc='XAXX010101000',payment_method='PUE',
                stamped_xml_base64=base64.b64encode(XML.encode()).decode()));session.commit()
        barrier=Barrier(2);mutex=Lock();calls={'sat':0,'pac':0}
        def sat(*args):
            with mutex:calls['sat']+=1;number=calls['sat']
            if number<=2:barrier.wait(timeout=10)
            return status()
        def pac(*args):
            with mutex:calls['pac']+=1
            return 200,ack()
        monkeypatch.setattr(svc,'sat_status',sat);monkeypatch.setattr(svc,'cancel_at_pac',pac)
        def submit(_):
            with Session(engine) as session:return svc.submit(session,'draft-a',CTX,FOLIO)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,(0,1)))
        assert calls['pac']==1 and all(r['status'] in ('processing','requested') for r in results)
        with Session(engine) as session:
            assert svc.read(session,'draft-a',CTX)['status']=='requested'
            with pytest.raises(DatabaseError):session.execute(text('UPDATE billing_live_cancel_events SET payload=payload'))
            session.rollback()
    finally:
        engine.dispose()
        with admin.begin() as connection:connection.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
