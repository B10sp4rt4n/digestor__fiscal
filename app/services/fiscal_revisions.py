"""Same-economic-value replacements; no rewriting of original evidence or cash."""
import base64
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET
from lxml import etree as E
from fastapi import HTTPException
from app.core.config import settings
from app.models.fiscal_revision import FiscalRevision, FiscalRevisionAct
from app.models.billing_cancellation import BillingCancellation
from app.models.billing_payment import BillingPayment, PaymentReceipt
from app.services.fiscal_acts import canonical, digest
from app.services.rep_validation import schemas, LOCK
from app.services.timbracfdi_client import extract_stamp_uuid

C = 'http://www.sat.gob.mx/cfd/4'
P = 'http://www.sat.gob.mx/Pagos20'
T = 'http://www.sat.gob.mx/TimbreFiscalDigital'
NS = {'c': C, 'p': P, 't': T}


def fail(message, status=409): raise HTTPException(status_code=status, detail=message)


def parse(xml):
    try:
        if not isinstance(xml, str) or len(xml) > 5_000_000 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper(): raise ValueError()
        return ET.fromstring(xml)
    except (ValueError, ET.ParseError): fail('XML fiscal no verificable')


def schema_valid(xml, signed=False):
    try:
        parse(xml)
        root = E.fromstring(xml.encode(), E.XMLParser(no_network=True, resolve_entities=False, load_dtd=False))
        official, unsigned, _, _ = schemas()
        with LOCK: return (official if signed else unsigned).validate(root)
    except (ValueError, TypeError, E.XMLSyntaxError, HTTPException): return False


def base_xml(db, draft, payment_id=None):
    if draft.stamp_status != 'stamped': fail('Se requiere una factura timbrada')
    encoded = draft.stamped_xml_base64
    if payment_id:
        payment = db.query(BillingPayment).filter_by(id=payment_id, company_id=draft.company_id, draft_id=draft.id).first()
        receipt = db.query(PaymentReceipt).filter_by(payment_id=payment_id, company_id=draft.company_id).first()
        if not payment or not receipt: fail('Pago no encontrado', 404)
        if receipt.status != 'stamped': fail('Primero confirma el complemento original')
        encoded = receipt.xml_base64
    try: xml = base64.b64decode(encoded, validate=True).decode()
    except (ValueError, TypeError): fail('XML original no verificable')
    root = parse(xml)
    if (root.tag != '{'+C+'}Comprobante' or not extract_stamp_uuid(xml)
            or root.get('TipoDeComprobante') != ('P' if payment_id else 'I')
            or root.find('c:Emisor', NS) is None or root.find('c:Receptor', NS) is None
            or root.find('c:Emisor', NS).get('Rfc') != draft.emitter_rfc
            or root.find('c:Receptor', NS).get('Rfc') != draft.customer_rfc):
        fail('Identidad del XML no coincide con la factura')
    return xml


def cancellation(db, draft, folio):
    return db.query(BillingCancellation).filter_by(company_id=draft.company_id, draft_id=draft.id, document_uuid=folio.lower()).first()


def context(db, draft, payment_id=None):
    xml = base_xml(db, draft, payment_id)
    history = []; visited = set()
    while True:
        folio = extract_stamp_uuid(xml).lower()
        if folio in visited or len(visited) >= 30: fail('Cadena de sustitución inconsistente')
        visited.add(folio)
        row = db.query(FiscalRevision).filter_by(company_id=draft.company_id, draft_id=draft.id,
            payment_id=payment_id, source_uuid=folio).filter(FiscalRevision.status != 'abandoned').first()
        if not row: return xml, None, history
        verify(db, row)
        if row.source_xml != xml: fail('Cambió la evidencia de origen de la sustitución')
        history.append(row)
        cancelled = cancellation(db, draft, folio)
        if row.status != 'stamped' or not cancelled or cancelled.status != 'cancelled':
            return xml, row, history
        if not row.result_xml or extract_stamp_uuid(row.result_xml).lower() != row.result_uuid:
            fail('Resultado de sustitución inconsistente')
        xml = row.result_xml


def payment_draft(db, draft):
    xml, _, _ = context(db, draft)
    root = parse(xml); customer = root.find('c:Receptor', NS)
    data = {column.name: getattr(draft, column.name) for column in draft.__table__.columns}
    data.update(stamped_xml_base64=base64.b64encode(xml.encode()).decode(),
        payment_method=root.get('MetodoPago'), payment_form=root.get('FormaPago'),
        customer_name=customer.get('Nombre'), customer_zip=customer.get('DomicilioFiscalReceptor'),
        customer_regimen=customer.get('RegimenFiscalReceptor'), customer_use_cfdi=customer.get('UsoCFDI'))
    data['_verified_revision_uuid'] = extract_stamp_uuid(xml).lower() if xml != base_xml(db, draft) else None
    return SimpleNamespace(**data)


def verify(db, row, expected_state=None):
    acts = db.query(FiscalRevisionAct).filter_by(company_id=row.company_id, revision_id=row.id).order_by(FiscalRevisionAct.sequence).all()
    declaration = json.loads(row.declaration)
    if declaration['source_uuid'] != row.source_uuid or declaration['idempotency_key'] != row.idempotency_key:
        fail('La declaración no corresponde a la sustitución')
    if not acts and expected_state != 'absent': fail('La sustitución no tiene evidencia de preparación')
    previous, state = None, None
    for sequence, act in enumerate(acts, 1):
        value = json.loads(act.payload)
        if (act.sequence != sequence or digest(act.payload) != act.content_hash or canonical(value) != act.payload
                or value['event_id'] != act.id or value['revision_id'] != row.id or value['tenant_id'] != row.company_id
                or value['sequence'] != sequence or value['previous_hash'] != previous
                or value['draft_id'] != row.draft_id or value['payment_id'] != row.payment_id
                or value['request_sha256'] != digest(row.request_xml) or value['source_sha256'] != digest(row.source_xml)
                or value['declaration_sha256'] != digest(row.declaration)
                or (state is not None and value['before'] != state) or (sequence == 1 and value['before'] != 'absent')):
            fail('Historial de sustitución alterado')
        previous, state = act.content_hash, value['after']
    expected = row.status if expected_state is None else expected_state
    if state is not None and state != expected: fail('Estado de sustitución no coincide con el historial')
    if expected == 'stamped' and (not row.result_xml or not acts or json.loads(acts[-1].payload).get('result_sha256') != digest(row.result_xml)):
        fail('XML de sustitución alterado')
    return acts, previous


def append(db, row, ctx, action, before, result=None):
    after = row.status
    transitions = {'absent': {'prepared'}, 'prepared': {'processing', 'abandoned'},
        'rejected': {'processing', 'abandoned'}, 'processing': {'prepared', 'unknown', 'rejected', 'stamped'},
        'unknown': {'stamped'}}
    if after not in transitions.get(before, set()): fail('Transición de corrección no permitida')
    acts, previous = verify(db, row, expected_state=before)
    eid = str(uuid.uuid4())
    value = canonical({'event_id': eid, 'revision_id': row.id, 'tenant_id': row.company_id,
        'draft_id':row.draft_id, 'payment_id':row.payment_id,
        'sequence': len(acts)+1, 'previous_hash': previous, 'actor_id': ctx.user_id, 'action': action,
        'before': before, 'after': after, 'recorded_at': datetime.now(timezone.utc).isoformat(),
        'request_sha256': digest(row.request_xml), 'source_sha256': digest(row.source_xml),
        'declaration_sha256': digest(row.declaration), 'result_sha256': digest(row.result_xml) if row.result_xml else None,
        'provider_response_sha256': digest(canonical(result)) if result is not None else None})
    db.add(FiscalRevisionAct(id=eid, revision_id=row.id, company_id=row.company_id, sequence=len(acts)+1,
        payload=value, content_hash=digest(value))); db.flush()


def create(db, draft, body, ctx, payment_id=None):
    declaration = canonical(body.model_dump(mode='json'))
    duplicate = db.query(FiscalRevision).filter_by(company_id=draft.company_id, idempotency_key=str(body.idempotency_key)).first()
    if duplicate:
        if duplicate.draft_id != draft.id or duplicate.payment_id != payment_id or duplicate.declaration != declaration:
            fail('La clave de corrección corresponde a otros datos')
        verify(db, duplicate); return duplicate
    xml, pending, _ = context(db, draft, payment_id)
    folio = extract_stamp_uuid(xml).lower()
    if folio != str(body.source_uuid): fail('El comprobante cambió; recarga antes de corregir')
    if pending: fail('Ya existe una sustitución pendiente para este comprobante')
    cancel = cancellation(db, draft, folio)
    if cancel: fail('El comprobante ya tiene una solicitud de cancelación; resuelve ese expediente antes de corregir')
    if not payment_id and db.query(BillingPayment).filter_by(company_id=draft.company_id, draft_id=draft.id).first():
        fail('La factura tiene pagos; su reasignación requiere un flujo económico separado')
    if payment_id:
        payment = db.query(BillingPayment).filter_by(id=payment_id, company_id=draft.company_id, draft_id=draft.id).one()
        if db.query(BillingPayment).filter(BillingPayment.company_id==draft.company_id,
                BillingPayment.draft_id==draft.id, BillingPayment.sequence>payment.sequence).first():
            fail('Primero resuelve las parcialidades posteriores')
    root = parse(xml)
    if not schema_valid(xml, signed=True): fail('El XML original no pasa el esquema fijado; requiere revisión', 422)
    rid = str(uuid.uuid4())
    for field in ('Sello', 'Certificado', 'NoCertificado'): root.attrib.pop(field, None)
    for element in root.findall('c:CfdiRelacionados', NS): root.remove(element)
    complement = root.find('c:Complemento', NS)
    if complement is not None:
        for stamp in list(complement):
            if stamp.tag == '{'+T+'}TimbreFiscalDigital': complement.remove(stamp)
        if not len(complement): root.remove(complement)
    related = ET.Element('{'+C+'}CfdiRelacionados', {'TipoRelacion':'04'})
    ET.SubElement(related, '{'+C+'}CfdiRelacionado', {'UUID':folio})
    root.insert(0, related)
    root.set('Fecha', datetime.now(ZoneInfo('America/Mexico_City')).strftime('%Y-%m-%dT%H:%M:%S'))
    root.set('Folio', rid); root.set('Serie', 'SRP' if payment_id else 'SF')
    changes = body.changes.model_dump(exclude_none=True)
    customer = root.find('c:Receptor', NS)
    if payment_id:
        if set(changes) - {'payment_form', 'operation_reference'}: fail('El REP sólo admite corregir forma y referencia conservando importes y fecha', 422)
        pago = root.find('c:Complemento/p:Pagos/p:Pago', NS)
        if pago is None or len(root.findall('c:Complemento/p:Pagos/p:Pago', NS)) != 1: fail('Perfil de REP fuera de alcance', 422)
        if 'payment_form' in changes:
            if changes['payment_form'] == '99': fail('El pago recibido requiere una forma conocida', 422)
            pago.set('FormaDePagoP', changes['payment_form'])
        if 'operation_reference' in changes: pago.set('NumOperacion', changes['operation_reference'])
    else:
        if 'operation_reference' in changes: fail('Referencia de operación sólo aplica a REP', 422)
        for key, attr in [('customer_name','Nombre'), ('customer_zip','DomicilioFiscalReceptor'), ('customer_regimen','RegimenFiscalReceptor'), ('customer_use_cfdi','UsoCFDI')]:
            if key in changes: customer.set(attr, changes[key])
        for key, attr in [('payment_method','MetodoPago'), ('payment_form','FormaPago')]:
            if key in changes: root.set(attr, changes[key])
        if root.get('MetodoPago') == 'PPD' and not settings.FISCAL_PPD_ENABLED: fail('PPD aún no está habilitado', 503)
        if (root.get('MetodoPago') == 'PPD') != (root.get('FormaPago') == '99'): fail('PPD requiere99; PUE requiere forma conocida', 422)
        if root.get('Moneda') != 'MXN': fail('Sustitución limitada a MXN', 422)
    request_xml = ET.tostring(root, encoding='unicode', xml_declaration=True)
    if not schema_valid(request_xml): fail('La corrección no pasa el esquema fiscal fijado', 422)
    row = FiscalRevision(id=rid, company_id=draft.company_id, draft_id=draft.id, payment_id=payment_id,
        source_uuid=folio, source_xml=xml, declaration=declaration, idempotency_key=str(body.idempotency_key),
        request_xml=request_xml, status='prepared')
    db.add(row); db.flush(); append(db, row, ctx, 'prepared', 'absent')
    return row


def validate_result(row, xml):
    if not schema_valid(xml, signed=True): return False
    expected, actual = parse(row.request_xml), parse(xml)
    if len(actual.findall('c:Complemento/t:TimbreFiscalDigital', NS)) != 1: return False
    def business(node):
        attrs = {k:v for k,v in node.attrib.items() if k != '{http://www.w3.org/2001/XMLSchema-instance}schemaLocation'
            and not (node.tag == '{'+C+'}Comprobante' and k in {'Sello','Certificado','NoCertificado'})}
        children = [business(child) for child in node if child.tag != '{'+T+'}TimbreFiscalDigital'
            and not (child.tag == '{'+C+'}Complemento' and not any(n.tag != '{'+T+'}TimbreFiscalDigital' for n in child))]
        return node.tag, sorted(attrs.items()), (node.text or '').strip(), children
    return business(expected) == business(actual) and extract_stamp_uuid(xml).lower() != row.source_uuid


def view(db, draft, payment_id=None):
    xml, pending, history = context(db, draft, payment_id)
    root = parse(xml); customer = root.find('c:Receptor', NS)
    items = []
    for row in history:
        cancel = cancellation(db, draft, row.source_uuid)
        items.append({'id':row.id, 'source_uuid':row.source_uuid, 'status':row.status, 'result_uuid':row.result_uuid,
            'source_cancellation_status':cancel.status if cancel else 'not_requested',
            'request_xml_base64':base64.b64encode(row.request_xml.encode()).decode(),
            'result_xml_base64':base64.b64encode(row.result_xml.encode()).decode() if row.result_xml else None,
            'reason':json.loads(row.declaration)['reason'],
            'changes':{k:v for k,v in json.loads(row.declaration)['changes'].items() if v is not None}})
    defaults = {'customer_name':customer.get('Nombre'), 'customer_zip':customer.get('DomicilioFiscalReceptor'),
        'customer_regimen':customer.get('RegimenFiscalReceptor'), 'customer_use_cfdi':customer.get('UsoCFDI'),
        'payment_method':root.get('MetodoPago'), 'payment_form':root.get('FormaPago')}
    if payment_id:
        pago = root.find('c:Complemento/p:Pagos/p:Pago', NS)
        defaults = {'payment_form':pago.get('FormaDePagoP'), 'operation_reference':pago.get('NumOperacion','')}
    archived = db.query(FiscalRevision).filter_by(company_id=draft.company_id, draft_id=draft.id,
        payment_id=payment_id, status='abandoned').all()
    archived_items = []
    for row in archived:
        verify(db, row)
        archived_items.append({'id':row.id, 'source_uuid':row.source_uuid, 'reason':json.loads(row.declaration)['reason']})
    return {'environment':settings.timbracfdi_environment, 'active_uuid':extract_stamp_uuid(xml).lower(), 'active_xml_base64':base64.b64encode(xml.encode()).decode(),
        'pending':pending is not None, 'kind':'receipt' if payment_id else 'invoice', 'defaults':defaults,
        'history':items, 'archived_history':archived_items, 'economic_payments_unchanged':True}
