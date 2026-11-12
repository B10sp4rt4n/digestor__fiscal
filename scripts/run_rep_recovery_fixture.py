"""Isolated PostgreSQL/HTTP REP acceptance fixture; no external provider traffic.

Requires a named disposable loopback database. Never imported by app.main.
"""
import os
os.environ.update(DATABASE_URL='sqlite:///:memory:', DB_URL='sqlite:///:memory:', DB_SSL_REQUIRE='false',
    FISCAL_MAINTENANCE_MODE='false',
    AUTH_CONNECTOR='jwt', AUTH_ALLOW_ANONYMOUS='false',
    FISCAL_PPD_ENABLED='true', FISCAL_REP_STAMP_ENABLED='true',
    FISCAL_PPD_PRODUCTION_ENABLED='false', TIMBRACFDI_ENV='sandbox',
    TIMBRACFDI_USE_SOAP='false', JWT_SECRET_KEY='synthetic-rep-fixture-only',
    TIMBRACFDI_TOKEN='', TIMBRACFDI_SANDBOX_TOKEN='', TIMBRACFDI_PRODUCTION_TOKEN='')
import argparse
import asyncio
import base64
import hashlib
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Barrier, Event, Lock
from uuid import uuid4
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1]/'tests')]
import requests
from fastapi import FastAPI, HTTPException
from hypercorn.asyncio import serve
from hypercorn.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.db.session import get_db
from app.api.routers import payments_v1
from app.models.commercial import BillingDraft
from app.models.billing_payment import BillingPayment, PaymentReceipt, PaymentAct
from app.services import timbracfdi_client as pac
from app.services.jwt_service import create_access_token
from test_payment_cycle import TABLES, CTX, accepted
from test_fiscal_revisions import seed_valid

url = make_url(os.environ['REP_FIXTURE_DATABASE_URL'])
if url.drivername != 'postgresql+psycopg' or url.host not in ('127.0.0.1', 'localhost', '::1') or url.database != 'digestor_fiscal_test':
    raise RuntimeError('Named disposable loopback PostgreSQL required')
admin = create_engine(url)
schema = 'rep_acceptance_'+uuid4().hex
with admin.begin() as conn:
    conn.execute(text(f'CREATE SCHEMA "{schema}"'))
engine = create_engine(url, connect_args={'options':'-csearch_path='+schema})
Session = sessionmaker(bind=engine)
Base.metadata.create_all(engine, tables=TABLES)
with Session() as db:
    invoice = seed_valid(db)
    original_xml = invoice.stamped_xml_base64

calls = {'stamp':0, 'retrieve':0}
issued = {}
gate, lock = Event(), Lock()
recovery_barrier = Barrier(2)
second_uuid = None
correct_retrievals = 0

def forbidden(*args, **kwargs):
    raise AssertionError('External network forbidden in synthetic REP fixture')
requests.sessions.Session.request = forbidden

def stamp(**kwargs):
    global second_uuid
    result = accepted(**kwargs)
    folio = pac.extract_stamp_uuid(result['provider_response']['Xml'])
    with lock:
        calls['stamp'] += 1
        attempt = calls['stamp']
        issued[folio] = result
        if attempt == 2:
            second_uuid = folio
    if attempt == 1:
        if not gate.wait(timeout=30):
            raise RuntimeError('Synthetic test did not release first provider call')
    elif attempt == 2:
        raise TimeoutError('Synthetic PAC accepted REP but reply was lost before persistence')
    else:
        raise AssertionError('A duplicate provider emission was attempted')
    return result

def retrieve(emitter, folio):
    global correct_retrievals
    with lock:
        calls['retrieve'] += 1
        paired = folio == second_uuid and correct_retrievals < 2
        if paired:
            correct_retrievals += 1
    if paired:
        recovery_barrier.wait(timeout=15)
    return issued.get(folio, {'ok':False, 'status_code':404})

pac.timbra_cfdi = stamp
pac.retrieve_cfdi = retrieve

@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        gate.set()
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()

app = FastAPI(lifespan=lifespan)
app.include_router(payments_v1.router)

def db_override():
    with Session() as db:
        yield db
app.dependency_overrides[get_db] = db_override

@app.post('/auth/login')
def login(body:dict):
    if body != {'username':'synthetic', 'password':'fixture-only'}:
        raise HTTPException(401)
    return {'access_token':create_access_token({'sub':CTX.user_id, 'tenant':CTX.tenant_id, 'role':'operator'}), 'expires_in':600}

@app.post('/__fixture/release')
def release():
    gate.set()
    return {'released':True}

@app.get('/__fixture')
def snapshot():
    with Session() as db:
        invoice = db.get(BillingDraft, 'credit-invoice')
        rows = db.query(BillingPayment).order_by(BillingPayment.sequence).all()
        receipts = db.query(PaymentReceipt).all()
        acts = [json.loads(row.payload) for row in db.query(PaymentAct).all()]
        return {'mode':'synthetic-postgres-rep-no-pac', 'calls':dict(calls),
            'issued':{folio:result['provider_response']['Xml'] for folio,result in issued.items()},
            'invoice':{'uuid':pac.extract_stamp_uuid(base64.b64decode(original_xml).decode()), 'xmlBase64':original_xml},
            'invoice_unchanged':invoice.stamped_xml_base64 == original_xml,
            'payments':[{'id':p.id, 'sequence':p.sequence, 'amount':str(p.amount),
                'balance_after':str(p.balance_after), 'declaration_hash':hashlib.sha256(p.declaration.encode()).hexdigest()} for p in rows],
            'receipts':[{'payment_id':r.payment_id, 'status':r.status, 'uuid':r.uuid,
                'xml_base64':r.xml_base64} for r in receipts],
            'recovered_acts':sum(a.get('action_or_state') == 'payment.recovered' for a in acts)}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    config = Config()
    config.bind = [f'127.0.0.1:{args.port}']
    config.accesslog = None
    asyncio.run(serve(app, config))
