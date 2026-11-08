"""Production routing and SAT boundaries, synthetic fixtures; never a real payment."""
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from app.core.config import settings
from app.core.fiscal_sandbox import ppd_transport_is_allowed
from app.api.routers import payments_v1 as api
from app.models.billing_payment import BillingPayment
from app.services import production_cancellation as live
from test_payment_cycle import db, seed, body, accepted, CTX
from scripts.install_fiscal_cycle import install

@pytest.fixture(autouse=True)
def production(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Network forbidden')))
    for key,value in {'TIMBRACFDI_ENV':'production','TIMBRACFDI_USE_SOAP':True,
        'TIMBRACFDI_SOAP_URL':'https://timbracfdi33.mx:1443/Timbrado.asmx',
        'TIMBRACFDI_TOKEN':'synthetic-token','FISCAL_PPD_ENABLED':True,
        'FISCAL_REP_STAMP_ENABLED':True,'FISCAL_PPD_PRODUCTION_ENABLED':True,
        'FISCAL_PPD_PRODUCTION_EMITTER_RFC':'EKU9003173C9'}.items():
        monkeypatch.setattr(settings,key,value)


def bind(monkeypatch,invoice):
    monkeypatch.setattr(settings,'FISCAL_PPD_PRODUCTION_EMITTER_RFC',invoice.emitter_rfc)


def test_production_partial_and_final_cycle(db,monkeypatch):
    invoice=seed(db);bind(monkeypatch,invoice);original=invoice.stamped_xml_base64
    sat=Mock(return_value={'Estado':'Vigente'});monkeypatch.setattr(live,'sat_status',sat)
    pac=Mock(side_effect=accepted);monkeypatch.setattr(api.pac,'timbra_cfdi',pac)
    for ref in ['synthetic-1','synthetic-2']:
        payment=api.register_payment(invoice.id,body('58.00',ref),CTX,db)
        assert api.stamp_receipt(invoice.id,payment['id'],CTX,db)['ok']
    summary=api.list_payments(invoice.id,CTX,db)
    assert summary['environment']=='production' and summary['balance']=='0.00'
    assert pac.call_count==2 and sat.call_count==4
    assert invoice.stamped_xml_base64==original
    with pytest.raises(HTTPException): api.stamp_receipt(invoice.id,payment['id'],CTX,db)
    assert pac.call_count==2

@pytest.mark.parametrize('state',['Cancelado','No Encontrado',''])
def test_sat_not_current_blocks_writes(db,monkeypatch,state):
    invoice=seed(db);bind(monkeypatch,invoice)
    monkeypatch.setattr(live,'sat_status',Mock(return_value={'Estado':state}))
    with pytest.raises(HTTPException) as exc: api.register_payment(invoice.id,body(),CTX,db)
    assert exc.value.status_code==409 and db.query(BillingPayment).count()==0


def test_sat_timeout_blocks_writes(db,monkeypatch):
    invoice=seed(db);bind(monkeypatch,invoice)
    monkeypatch.setattr(live,'sat_status',Mock(side_effect=TimeoutError))
    with pytest.raises(HTTPException) as exc: api.register_payment(invoice.id,body(),CTX,db)
    assert exc.value.status_code==502 and db.query(BillingPayment).count()==0

@pytest.mark.parametrize('key,value',[
    ('FISCAL_PPD_PRODUCTION_ENABLED',False),('FISCAL_PPD_PRODUCTION_EMITTER_RFC',''),
    ('TIMBRACFDI_SOAP_URL','https://timbracfdi33.mx:1443/Timbrado.asmx?redirect=1'),
    ('TIMBRACFDI_SOAP_URL','https://evil.test:1443/Timbrado.asmx'),('TIMBRACFDI_ENV','bogus')])
def test_production_fail_closed(monkeypatch,key,value):
    monkeypatch.setattr(settings,key,value)
    assert not ppd_transport_is_allowed('AAA010101AAA')


def test_different_emitter_is_rejected(db,monkeypatch):
    invoice=seed(db);monkeypatch.setattr(settings,'FISCAL_PPD_PRODUCTION_EMITTER_RFC','OTHER010101AAA')
    with pytest.raises(HTTPException) as exc: api.register_payment(invoice.id,body(),CTX,db)
    assert exc.value.status_code==403 and db.query(BillingPayment).count()==0


def test_additive_installer_preserves_original_and_live_cancellation():
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE billing_draft (id TEXT PRIMARY KEY, company_id TEXT NOT NULL, stamped_xml_base64 TEXT)'))
        conn.execute(text("INSERT INTO billing_draft VALUES ('existing','tenant','original-bytes')"))
    live.install(engine)
    with engine.begin() as conn:
        conn.execute(live.intents.insert().values(company_id='tenant',uuid='synthetic-uuid',draft_id='existing',xml_sha256='original-hash',state='cancelled',intent='{}'))
    install(engine);install(engine)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT stamped_xml_base64 FROM billing_draft')).scalar()=='original-bytes'
        assert conn.execute(text('SELECT state FROM billing_live_cancel_requests')).scalar()=='cancelled'
        assert conn.execute(text('SELECT count(*) FROM billing_payments')).scalar()==0
        assert inspect(conn).has_table('billing_fiscal_acts')
    engine.dispose()


def test_soap_retrieval_uses_documented_fields_without_redirects(monkeypatch):
    import xml.etree.ElementTree as ET
    from app.services import timbracfdi_client as pac
    monkeypatch.setattr(pac,'_require_configured',lambda:None)
    post=Mock(return_value=Mock(status_code=200,ok=True,text='<anyType>0</anyType><anyType>synthetic-xml</anyType>'))
    monkeypatch.setattr(pac.requests,'post',post)
    pac.retrieve_cfdi('SYNTHETIC','synthetic-uuid')
    args=post.call_args.kwargs;root=ET.fromstring(args['data']);ns={'s':'http://schemas.xmlsoap.org/soap/envelope/','p':'http://tempuri.org/'}
    operation=root.find('s:Body/p:ObtieneCFDI',ns)
    assert [child.tag.split('}')[-1] for child in operation]==['usuarioIntegrador','rfcEmisor','folioUUID']
    assert operation.find('p:folioUUID',ns).text=='synthetic-uuid'
    assert args['allow_redirects'] is False


from test_billing_stamp_integration import pg_engine

def test_installer_postgres_repeat_preserves_records_and_append_only(pg_engine):
    from sqlalchemy.exc import DBAPIError
    live.install(pg_engine)
    with pg_engine.connect() as conn:
        before=conn.execute(text('SELECT id,company_id,stamped_xml_base64 FROM billing_draft ORDER BY id')).all()
    install(pg_engine);install(pg_engine)
    with pg_engine.connect() as conn:
        assert conn.execute(text('SELECT id,company_id,stamped_xml_base64 FROM billing_draft ORDER BY id')).all()==before
        assert conn.execute(text('SELECT count(*) FROM billing_payments')).scalar()==0
    for table in ['billing_payments','billing_payment_acts','billing_fiscal_acts','billing_cancellation_acts','billing_fiscal_revision_acts']:
        with pytest.raises(DBAPIError):
            with pg_engine.begin() as conn: conn.execute(text(f'DELETE FROM {table}'))
    live.assert_schema(pg_engine)
