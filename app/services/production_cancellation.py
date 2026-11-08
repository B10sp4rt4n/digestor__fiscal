"""Audited cancellation of one explicitly configured production PUE invoice.

Independent of the PPD/sandbox development stack. Never changes an issued XML,
commercial outcome or cash. An acknowledgement is not confirmation by the SAT.
"""
import base64
import hashlib
import html
import json
from datetime import datetime, timezone
from urllib.parse import urlparse
from xml.etree import ElementTree as E

import requests
from fastapi import HTTPException
from sqlalchemy import (MetaData, Table, Column, String, Text, Integer, ForeignKeyConstraint,
                        UniqueConstraint, insert, select, update, text, inspect)
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.models.commercial import BillingDraft

metadata = MetaData()
intents = Table('billing_live_cancel_requests', metadata,
    Column('company_id', String, primary_key=True), Column('uuid', String, primary_key=True),
    Column('draft_id', String, nullable=False), Column('xml_sha256', String, nullable=False),
    Column('state', String, nullable=False), Column('intent', Text, nullable=False),
    Column('provider_response', Text), Column('ack', Text), Column('sat_response', Text))
events = Table('billing_live_cancel_events', metadata,
    Column('company_id', String, primary_key=True), Column('uuid', String, primary_key=True),
    Column('sequence', Integer, primary_key=True), Column('payload', Text, nullable=False),
    Column('content_hash', String, nullable=False),
    ForeignKeyConstraint(['company_id','uuid'], ['billing_live_cancel_requests.company_id','billing_live_cancel_requests.uuid']))


def fail(message, status=409):
    raise HTTPException(status_code=status, detail=message)


def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def install(engine):
    """Add only two new tables and their immutable event guards; no legacy edits."""
    with engine.begin() as conn:
        metadata.create_all(conn)
        if conn.dialect.name == 'postgresql':
            conn.execute(text("""CREATE OR REPLACE FUNCTION live_cancel_events_immutable() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Live cancellation evidence is immutable'; END $$"""))
            conn.execute(text('DROP TRIGGER IF EXISTS live_cancel_events_immutable ON billing_live_cancel_events'))
            conn.execute(text('CREATE TRIGGER live_cancel_events_immutable BEFORE UPDATE OR DELETE ON billing_live_cancel_events FOR EACH ROW EXECUTE FUNCTION live_cancel_events_immutable()'))
        elif conn.dialect.name == 'sqlite':
            for op in ('UPDATE', 'DELETE'):
                conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS live_cancel_no_{op.lower()} BEFORE {op} ON billing_live_cancel_events BEGIN SELECT RAISE(ABORT, 'Immutable cancellation evidence'); END"))
        else:
            raise RuntimeError('Unsupported audit database')


def assert_schema(engine):
    db = inspect(engine)
    if not all(db.has_table(table) for table in metadata.tables):
        raise RuntimeError('Install reviewed production cancellation audit tables before enabling')


def enabled():
    if not settings.LIVE_CANCEL_ENABLED:
        fail('Cancelación productiva no habilitada', 503)
    if (settings.TIMBRACFDI_ENV.strip().lower() != 'production'
            or not settings.LIVE_CANCEL_DRAFT_ID or not settings.LIVE_CANCEL_UUID
            or len(settings.LIVE_CANCEL_XML_SHA256) != 64):
        fail('Falta configurar el piloto productivo exacto', 503)
    try:
        u = urlparse(settings.TIMBRACFDI_SOAP_URL)
        valid = (u.scheme == 'https' and u.hostname == 'timbracfdi33.mx' and u.port == 1443
            and u.path == '/Timbrado.asmx' and not u.username and not u.password and not u.query and not u.fragment)
    except ValueError:
        valid = False
    if not valid or not settings.timbracfdi_active_token:
        fail('Transporte productivo del PAC no verificable', 503)


def parse(xml):
    if not xml or len(xml) > 2_000_000 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        fail('XML no verificable')
    try: return E.fromstring(xml)
    except E.ParseError: fail('XML no verificable')


def source(db, draft_id, ctx):
    enabled()
    if draft_id != settings.LIVE_CANCEL_DRAFT_ID:
        fail('Documento fuera del piloto autorizado', 404)
    draft = db.query(BillingDraft).filter_by(id=draft_id, company_id=ctx.tenant_id).first()
    if not draft: fail('Factura no encontrada', 404)
    try: xml = base64.b64decode(draft.stamped_xml_base64, validate=True).decode()
    except (ValueError, TypeError): fail('No se pudo leer el XML original')
    root = parse(xml)
    ns = {'c':'http://www.sat.gob.mx/cfd/4','t':'http://www.sat.gob.mx/TimbreFiscalDigital'}
    stamps = root.findall('c:Complemento/t:TimbreFiscalDigital', ns)
    emitter, recipient = root.find('c:Emisor', ns), root.find('c:Receptor', ns)
    if (draft.stamp_status != 'stamped' or root.tag != '{'+ns['c']+'}Comprobante'
            or root.get('TipoDeComprobante') != 'I' or root.get('MetodoPago') != 'PUE'
            or len(stamps) != 1 or stamps[0].get('UUID', '').lower() != settings.LIVE_CANCEL_UUID.lower()
            or digest(xml) != settings.LIVE_CANCEL_XML_SHA256 or emitter is None or recipient is None
            or emitter.get('Rfc') != draft.emitter_rfc or recipient.get('Rfc') != draft.customer_rfc
            or root.find('c:CfdiRelacionados', ns) is not None):
        fail('El comprobante no corresponde al XML autorizado')
    # Existing production schema predates collections. Never overlook later records.
    if inspect(db.bind).has_table('billing_payments'):
        count = db.execute(text('SELECT count(*) FROM billing_payments WHERE draft_id=:id'), {'id':draft.id}).scalar_one()
        if count: fail('Factura con cobros registrados; requiere otro procedimiento')
    return draft, root, xml, settings.LIVE_CANCEL_UUID.lower()


def row(db, company, folio):
    return db.execute(select(intents).where(intents.c.company_id == company, intents.c.uuid == folio).with_for_update()).mappings().first()


def history(db, item):
    rows = db.execute(select(events).where(events.c.company_id == item['company_id'], events.c.uuid == item['uuid']).order_by(events.c.sequence)).mappings().all()
    previous, state = None, 'absent'
    for number, entry in enumerate(rows, 1):
        data = json.loads(entry['payload'])
        if (entry['sequence'] != number or digest(entry['payload']) != entry['content_hash']
                or canonical(data) != entry['payload'] or data['previous_hash'] != previous
                or data['before'] != state or data['company_id'] != item['company_id']
                or data['uuid'] != item['uuid'] or data['draft_id'] != item['draft_id']
                or data['xml_sha256'] != item['xml_sha256'] or data['intent_sha256'] != digest(item['intent'])):
            fail('Evidencia de cancelación inconsistente')
        previous, state = entry['content_hash'], data['after']
    if state != item['state']: fail('Estado sin evidencia consistente')
    if rows:
        last = json.loads(rows[-1]['payload'])
        for field in ('ack', 'provider_response', 'sat_response'):
            if last[field+'_sha256'] != digest(item[field] or ''):
                fail('Respuesta de cancelación alterada')
    return rows, previous


def record(db, item, ctx, action, before, previous, sequence):
    data = {k:item[k] for k in ('company_id','uuid','draft_id','xml_sha256')}
    data.update(action=action, before=before, after=item['state'], actor=ctx.user_id,
        intent_sha256=digest(item['intent']), previous_hash=previous,
        recorded_at=datetime.now(timezone.utc).isoformat(),
        **{field+'_sha256':digest(item[field] or '') for field in ('ack','provider_response','sat_response')})
    payload = canonical(data)
    db.execute(insert(events).values(company_id=item['company_id'],uuid=item['uuid'],sequence=sequence,
        payload=payload,content_hash=digest(payload)))


def soap_body(operation, fields):
    root = E.Element('{http://schemas.xmlsoap.org/soap/envelope/}Envelope')
    body = E.SubElement(root, '{http://schemas.xmlsoap.org/soap/envelope/}Body')
    op = E.SubElement(body, '{http://tempuri.org/}'+operation)
    for key, value in fields.items(): E.SubElement(op, '{http://tempuri.org/}'+key).text = value
    return E.tostring(root, encoding='utf-8', xml_declaration=True)


def sat_status(root, folio):
    ns = '{http://www.sat.gob.mx/cfd/4}'
    query = '?id='+folio+'&re='+root.find(ns+'Emisor').get('Rfc')+'&rr='+root.find(ns+'Receptor').get('Rfc')
    query += '&tt='+root.get('Total')+'&fe='+root.get('Sello', '')[-8:]
    response = requests.post('https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc',
        data=soap_body('Consulta', {'expresionImpresa':query}), headers={'Content-Type':'text/xml; charset=utf-8',
        'SOAPAction':'"http://tempuri.org/IConsultaCFDIService/Consulta"'}, timeout=(10,30), allow_redirects=False)
    if response.status_code != 200: fail('SAT sin confirmar; no repetir la cancelación', 502)
    result = parse(response.text)
    values = {n.tag.rsplit('}',1)[-1]:n.text for n in result.iter()
        if n.tag.rsplit('}',1)[-1] in ('CodigoEstatus','Estado','EsCancelable','EstatusCancelacion')}
    if not (values.get('CodigoEstatus') or '').startswith('S -') or values.get('Estado') not in ('Vigente','Cancelado'):
        fail('El SAT no confirmó el comprobante consultado', 502)
    return values


def cancel_at_pac(emitter, folio):
    # WSDL: the 40 operation includes motivo/folioSustitucion; old operation does not.
    fields = {'usuarioIntegrador':settings.timbracfdi_active_token, 'rfcEmisor':emitter,
        'folioUUID':folio, 'motivoCancelacion':'03', 'folioUUIDSustitucion':''}
    response = requests.post(settings.TIMBRACFDI_SOAP_URL, data=soap_body('CancelaCFDIAck40', fields),
        headers={'Content-Type':'text/xml; charset=utf-8','SOAPAction':'"http://tempuri.org/CancelaCFDIAck40"'},
        timeout=(10,45), allow_redirects=False)
    return response.status_code, response.text.replace(settings.timbracfdi_active_token, '[REDACTED]')[:2_000_000]


def acknowledgement(response, folio, emitter):
    try:
        outer = parse(response)
        candidates = [response] + [''.join(n.itertext()) for n in outer.iter() if n.tag.rsplit('}',1)[-1] == 'anyType']
        for value in candidates:
            for _ in range(3):
                value = html.unescape(value)
                try: doc = parse(value)
                except HTTPException: continue
                for ack in doc.iter():
                    if ack.tag.rsplit('}',1)[-1] != 'Acuse' or ack.get('RfcEmisor') != emitter: continue
                    for part in ack.iter():
                        children = {n.tag.rsplit('}',1)[-1]:n.text for n in part}
                        if children.get('UUID','').lower() == folio and children.get('EstatusUUID') in ('201','202'):
                            return E.tostring(ack, encoding='unicode')
    except (HTTPException, E.ParseError): pass
    return None


def view(item):
    if not item: return {'status':'not_requested','reason':'03','acknowledgement_xml':None}
    return {'status':item['state'],'document_uuid':item['uuid'],'reason':'03',
        'acknowledgement_xml':item['ack'], 'sat_response':json.loads(item['sat_response']) if item['sat_response'] else None,
        'economic_payments_unchanged':True}


def read(db, draft_id, ctx):
    draft, _, _, folio = source(db, draft_id, ctx)
    item = row(db, draft.company_id, folio)
    if item: history(db, item)
    return view(item)


def submit(db, draft_id, ctx, expected_uuid):
    draft, root, xml, folio = source(db, draft_id, ctx)
    if expected_uuid.lower() != folio: fail('Confirma el UUID exacto', 422)
    existing = row(db, draft.company_id, folio)
    if existing:
        history(db, existing)
        return view(existing)  # Never resend, even after a timeout or unknown response.
    status = sat_status(root, folio)
    if status['Estado'] != 'Vigente' or status.get('EsCancelable') != 'Cancelable sin aceptación':
        fail('El SAT no confirmó que sea cancelable sin aceptación')
    item = dict(company_id=draft.company_id,uuid=folio,draft_id=draft.id,xml_sha256=digest(xml),state='processing',
        intent=canonical({'reason':'03','uuid':folio,'xml_sha256':digest(xml),'initial_sat':status}),
        ack=None,provider_response=None,sat_response=canonical(status))
    try:
        db.execute(insert(intents).values(**item))
        record(db,item,ctx,'reserved','absent',None,1)
        db.commit()  # Durable reservation before the first cancellation request.
    except IntegrityError:
        db.rollback()
        existing = row(db,draft.company_id,folio); history(db,existing)
        return view(existing)
    response, ack = None, None
    try:
        code, response = cancel_at_pac(draft.emitter_rfc, folio)
        if code == 200: ack = acknowledgement(response,folio,draft.emitter_rfc)
    except requests.RequestException: pass
    current = db.execute(select(intents).where(intents.c.company_id==draft.company_id,intents.c.uuid==folio).with_for_update()).mappings().one()
    if current['state'] != 'processing': return view(current)
    acts, previous = history(db,current)
    next_item = dict(current, provider_response=response,ack=ack,state='requested' if ack else 'unknown')
    claimed = db.execute(update(intents).where(intents.c.company_id==draft.company_id,intents.c.uuid==folio,
        intents.c.state=='processing').values(state=next_item['state'],provider_response=response,ack=ack)).rowcount
    if claimed:
        record(db,next_item,ctx,'provider_response','processing',previous,len(acts)+1); db.commit()
    else: db.rollback()
    return reconcile(db,draft_id,ctx)


def reconcile(db,draft_id,ctx):
    draft, root, _, folio = source(db,draft_id,ctx)
    item = row(db,draft.company_id,folio)
    if not item: fail('No existe solicitud de cancelación')
    history(db,item)
    if item['state']=='cancelled': return view(item)
    # Query outside locks; then lock the one request to serialize audit append.
    db.commit()
    status = sat_status(root,folio)
    item = db.execute(select(intents).where(intents.c.company_id==draft.company_id,intents.c.uuid==folio).with_for_update()).mappings().one()
    acts, previous = history(db,item)
    if item['state']=='cancelled': return view(item)
    next_item = dict(item, state='cancelled' if status['Estado']=='Cancelado' else item['state'],sat_response=canonical(status))
    db.execute(update(intents).where(intents.c.company_id==draft.company_id,intents.c.uuid==folio).values(state=next_item['state'],sat_response=next_item['sat_response']))
    record(db,next_item,ctx,'sat_consulted',item['state'],previous,len(acts)+1); db.commit()
    return view(next_item)


if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['--install']: raise SystemExit('Only --install is supported')
    from app.db.session import engine
    install(engine)
    assert_schema(engine)
    print('Production cancellation audit schema ready; no PAC call.')
