"""Loopback-only HTTP acceptance fixture. In-memory synthetic data; PAC network forbidden.
Run with .venv/bin/python scripts/run_fiscal_cycle_fixture.py --port 8769.
Never deploy this test server or import it into the production application.
"""
import os
# Fix every runtime destination before application imports; never inherit real credentials.
os.environ.update(DATABASE_URL='sqlite:///:memory:', AUTH_CONNECTOR='jwt',
    FISCAL_CORRECTIONS_ENABLED='true', FISCAL_PPD_ENABLED='true', JWT_SECRET_KEY='synthetic-fixture-only-not-a-real-key', FISCAL_REP_STAMP_ENABLED='true', FISCAL_CANCELLATION_ENABLED='true',
    TIMBRACFDI_ENV='sandbox', TIMBRACFDI_USE_SOAP='false', TIMBRACFDI_TOKEN='',
    TIMBRACFDI_SANDBOX_TOKEN='', TIMBRACFDI_PRODUCTION_TOKEN='')
import argparse
import asyncio
import base64
import sys
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1]/'tests')]
import requests
from fastapi import FastAPI, HTTPException
from hypercorn.asyncio import serve
from hypercorn.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.base import Base
from app.db.session import get_db
from app.api.routers import payments_v1, cancellations_v1, fiscal_revisions_v1
from app.models.commercial import ProductCatalog, BillingDraft, BillingDraftItem
from app.models.fiscal_act import FiscalAct
from app.models.fiscal_revision import FiscalRevision, FiscalRevisionAct
from app.models.billing_payment import BillingPayment, PaymentReceipt, PaymentAct
from app.models.billing_cancellation import BillingCancellation, CancellationAct
from app.services import timbracfdi_client as pac
from app.services.jwt_service import create_access_token
from test_fiscal_revisions import seed_valid as seed, accepted, CTX

engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread':False}, poolclass=StaticPool)
Base.metadata.create_all(engine, tables=[m.__table__ for m in (ProductCatalog, BillingDraft, BillingDraftItem,
    FiscalRevision, FiscalRevisionAct, FiscalAct, BillingPayment, PaymentReceipt, PaymentAct, BillingCancellation, CancellationAct)])
Session = sessionmaker(bind=engine)
with Session() as db:
    original = seed(db)
    root = ET.fromstring(base64.b64decode(original.stamped_xml_base64))
    root.find('{http://www.sat.gob.mx/cfd/4}Complemento')[0].set('UUID', str(uuid4()))
    db.add(BillingDraft(id='cancel-invoice', company_id=CTX.tenant_id, stamp_status='stamped', status='stamped',
        emitter_rfc=original.emitter_rfc, customer_rfc=original.customer_rfc, total=116, subtotal=100, taxes=16, payment_method='PPD', payment_form='99',
        stamped_xml_base64=base64.b64encode(ET.tostring(root)).decode()))
    db.commit()

calls = {'stamp':0, 'cancel':0, 'query':0, 'retrieve':0}
pending = {}; last_uuid = None

def forbidden(*args, **kwargs): raise AssertionError('External PAC/network forbidden in HTTP fixture')
requests.sessions.Session.request = forbidden

def stamp(**kwargs):
    global last_uuid
    calls['stamp'] += 1
    result = accepted(**kwargs)
    last_uuid = pac.extract_stamp_uuid(result['provider_response']['Xml'])
    pending[last_uuid] = result
    if calls['stamp'] == 2: raise TimeoutError('Synthetic lost response after provider issued the second REP')
    return result

def cancel(emitter, folio, reason, replacement_uuid=None):
    calls['cancel'] += 1
    return {'ok':True,'status_code':200,'provider_response':{'XmlAcuse':f'<Acuse RfcEmisor="{emitter}"><Folios><UUID>{folio}</UUID><EstatusUUID>201</EstatusUUID></Folios></Acuse>'}}

def query(folio):
    calls['query'] += 1
    return {'ok':True,'status_code':200,'provider_response':{'Codigo':0,'RespuestaSat':{
        'UUID':folio,'EstadoSat':'Cancelado','CodigoEstatusSat':'S - Comprobante obtenido satisfactoriamente.'}}}

def retrieve(emitter, folio):
    calls['retrieve'] += 1
    return pending.get(folio, {'ok':False,'status_code':404})

pac.timbra_cfdi = stamp; pac.cancel_cfdi = cancel; pac.query_cfdi_status = query; pac.retrieve_cfdi = retrieve
app = FastAPI()
app.include_router(payments_v1.router); app.include_router(cancellations_v1.router); app.include_router(fiscal_revisions_v1.router)
def db_override():
    with Session() as db: yield db
app.dependency_overrides[get_db] = db_override

@app.post('/auth/login')
def login(body: dict):
    if body != {'username':'synthetic','password':'fixture-only'}: raise HTTPException(401)
    return {'access_token':create_access_token({'sub':CTX.user_id,'tenant':CTX.tenant_id,'role':'operator'}),'expires_in':600}

@app.get('/__fixture')
def fixture(): return {'synthetic':True,'calls':calls,'last_uuid':last_uuid}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--port', type=int, default=8769)
    args = parser.parse_args()
    config = Config(); config.bind = [f'127.0.0.1:{args.port}']; config.accesslog = None
    asyncio.run(serve(app, config))
