"""Loopback-only synthetic provider fixture. Never starts the production app/PAC."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['AUTH_CONNECTOR'] = 'jwt'
os.environ['AUTH_ALLOW_ANONYMOUS'] = 'false'
os.environ['JWT_SECRET_KEY'] = 'synthetic-loopback-only-never-production'

import base64
from collections import Counter
from xml.etree import ElementTree as ET
import requests
import asyncio
from hypercorn.asyncio import serve
from hypercorn.config import Config
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api.routers import commercial_v1 as api
from app.db.base import Base
from app.db.session import get_db
from app.models.commercial import BillingDraft, BillingDraftItem, ProductCatalog
from app.models.evento import EventoFacturacion
from app.models.fiscal_act import FiscalAct
from app.models.csf import CSF
from app.models.sucursal import Sucursal
from app.models.usuario import Usuario
from app.services.jwt_service import create_access_token

def forbidden(*args, **kwargs):
    raise AssertionError('External provider/network forbidden in synthetic fixture')
requests.sessions.Session.request = forbidden
engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)
Base.metadata.create_all(engine, tables=[ProductCatalog.__table__, BillingDraft.__table__, BillingDraftItem.__table__, EventoFacturacion.__table__, FiscalAct.__table__])
with Session() as db:
    for name in ('normal', 'lost', 'not-sent', 'rejected'):
        db.add(BillingDraft(id='synthetic-'+name, company_id='synthetic-a', status='ready_to_stamp',
            customer_name='SYNTHETIC CUSTOMER', customer_rfc='AAA010101AAA', customer_zip='01000', customer_regimen='616', customer_use_cfdi='S01',
            emitter_rfc='EKU9003173C9', emitter_name='SYNTHETIC EMITTER', emitter_regimen='601', place_of_issue='01000',
            payment_method='PUE', payment_form='03', subtotal=100, taxes=16, total=116,
            items=[BillingDraftItem(company_id='synthetic-a', description='Synthetic service', quantity=1,
                unit_price=100, line_subtotal=100, line_taxes=16, line_total=116)]))
    db.commit()
sent, retrieved, issued = Counter(), Counter(), {}
CFDI, TFD = 'http://www.sat.gob.mx/cfd/4', 'http://www.sat.gob.mx/TimbreFiscalDigital'
UUIDS = {'synthetic-normal': '11111111-2222-4333-8444-555555555555', 'synthetic-lost': '22222222-2222-4333-8444-555555555555'}
def response(xml): return {'ok': True, 'status_code': 200, 'provider_response': {'Codigo': 0, 'Xml': xml}}
def issue(*, xml_base64, id_comprobante):
    draft_id = id_comprobante.removeprefix('draft-'); sent[draft_id] += 1
    if draft_id == 'synthetic-rejected':
        return {'ok': True, 'status_code': 200, 'provider_response': {'Codigo': 301, 'Mensaje': 'Synthetic rejection'}}
    root = ET.fromstring(base64.b64decode(xml_base64))
    root.set('Sello', 'SINTETICO'*35); root.set('Certificado', 'c3ludGhldGlj'); root.set('NoCertificado', '0'*20)
    ET.SubElement(ET.SubElement(root, '{'+CFDI+'}Complemento'), '{'+TFD+'}TimbreFiscalDigital', {
        'UUID': UUIDS[draft_id], 'Version': '1.1', 'FechaTimbrado': '2026-11-09T12:01:00',
        'RfcProvCertif': 'AAA010101AAA', 'SelloCFD': 'c3ludGhldGlj', 'NoCertificadoSAT': '0'*20, 'SelloSAT': 'SINTETICO'*35})
    xml = ET.tostring(root, encoding='unicode'); issued[UUIDS[draft_id]] = xml
    if draft_id == 'synthetic-lost': raise TimeoutError('Synthetic response lost after acceptance')
    return response(xml)
def retrieve(emitter, uuid):
    retrieved[uuid] += 1
    return response(issued.get(uuid, ''))
api.timbracfdi_client.timbra_cfdi = issue
api.timbracfdi_client.retrieve_cfdi = retrieve
app = FastAPI(); app.include_router(api.router)
def session():
    with Session() as db: yield db
app.dependency_overrides[get_db] = session
@app.post('/auth/login')
def login(body: dict):
    if body != {'username': 'synthetic', 'password': 'synthetic-loopback-only'}: raise HTTPException(401)
    return {'access_token': create_access_token({'sub': 'synthetic-operator', 'tenant': 'synthetic-a', 'role': 'operator'}), 'expires_in': 3600}
@app.get('/__fixture')
def state():
    with Session() as db:
        return {'mode': 'synthetic-loopback-no-pac', 'sent': dict(sent), 'retrieved': dict(retrieved),
            'metrics': db.query(EventoFacturacion).count(), 'issued': issued}
if __name__ == '__main__':
    config = Config(); config.bind = ['127.0.0.1:'+str(int(sys.argv[1]))]; config.accesslog = None
    asyncio.run(serve(app, config))
