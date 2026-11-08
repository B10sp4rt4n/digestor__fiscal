"""Fail-closed sandbox cancellation; request acknowledgement is not SAT status."""
import base64
import json
import uuid
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from fastapi import HTTPException
from app.models.billing_cancellation import BillingCancellation, CancellationAct
from app.models.billing_payment import BillingPayment, PaymentReceipt
from app.models.commercial import BillingDraft
from app.services.fiscal_acts import canonical, digest
from app.services.timbracfdi_client import extract_stamp_uuid
from app.services import fiscal_revisions as revisions
from app.models.fiscal_revision import FiscalRevision

NS = {'c': 'http://www.sat.gob.mx/cfd/4'}
BLOCKING = {'processing', 'requested', 'unknown', 'cancelled'}


def fail(message, status=409):
    raise HTTPException(status_code=status, detail=message)


def parse_document(encoded):
    try:
        if not encoded or len(encoded) > 7_000_000: raise ValueError()
        xml = base64.b64decode(encoded, validate=True).decode('utf-8')
        if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper(): raise ValueError()
        root = ET.fromstring(xml)
        folio = extract_stamp_uuid(xml)
        if root.tag != '{'+NS['c']+'}Comprobante' or not folio: raise ValueError()
        return root, folio.lower(), digest(xml)
    except (ValueError, TypeError, ET.ParseError):
        fail('No se pudo verificar el XML timbrado original')


def target(db, draft, payment_id=None):
    encoded = draft.stamped_xml_base64
    if draft.stamp_status != 'stamped': fail('Se requiere factura timbrada')
    if payment_id:
        payment = db.query(BillingPayment).filter_by(id=payment_id, company_id=draft.company_id, draft_id=draft.id).first()
        receipt = db.query(PaymentReceipt).filter_by(payment_id=payment_id, company_id=draft.company_id).first()
        if not payment or not receipt: fail('Complemento no encontrado', 404)
        if receipt.status != 'stamped': fail('Se requiere complemento timbrado')
        encoded = receipt.xml_base64
    root, folio, content_hash = parse_document(encoded)
    emitter, customer = root.find('c:Emisor', NS), root.find('c:Receptor', NS)
    if emitter is None or customer is None or emitter.get('Rfc') != draft.emitter_rfc or customer.get('Rfc') != draft.customer_rfc:
        fail('Identidad del XML original no coincide con la factura')
    if root.get('TipoDeComprobante') != ('P' if payment_id else 'I'):
        fail('Tipo de comprobante fuera del alcance')
    effective_xml, _, _ = revisions.context(db, draft, payment_id)
    return parse_document(base64.b64encode(effective_xml.encode()).decode())


def find(db, draft, payment_id=None, lock=False, document_uuid=None):
    if not document_uuid and payment_id:
        receipt = db.query(PaymentReceipt).filter_by(payment_id=payment_id, company_id=draft.company_id).first()
        if not receipt or receipt.status != 'stamped': return None
    folio = document_uuid or target(db, draft, payment_id)[1]
    query = db.query(BillingCancellation).filter_by(company_id=draft.company_id, draft_id=draft.id,
        payment_id=payment_id, document_uuid=folio)
    if lock: query = query.with_for_update().populate_existing()
    return query.first()


def view(row):
    if not row: return {'status': 'not_requested', 'acknowledgement_xml': None}
    body = json.loads(row.intent)
    return {'status': row.status, 'document_uuid': row.document_uuid, 'reason': body['reason'],
            'idempotency_key': row.idempotency_key,
            'replacement_uuid': body['replacement_uuid'], 'acknowledgement_xml': row.acknowledgement_xml,
            'checked_at': row.checked_at.isoformat()+'Z' if row.checked_at else None,
            'economic_payments_unchanged': True}


def assert_invoice_active(db, draft):
    _, pending, _ = revisions.context(db, draft)
    if pending: fail('Hay una sustitución de factura pendiente; confirma su emisión y cancelación original')
    row = find(db, draft)
    if row and row.status in BLOCKING: fail('Factura cancelada o con cancelación pendiente; no registrar ni timbrar nuevos pagos')


def assert_receipt_active(db, draft, payment_id):
    assert_invoice_active(db, draft)
    receipt = db.query(PaymentReceipt).filter_by(company_id=draft.company_id, payment_id=payment_id).first()
    if receipt and receipt.status == 'stamped':
        _, pending, _ = revisions.context(db, draft, payment_id)
        if pending: fail('Hay una corrección de complemento pendiente; resuélvela antes de continuar')
    row = find(db, draft, payment_id)
    if row and row.status in BLOCKING: fail('Complemento cancelado o con cancelación pendiente; requiere revisión fiscal')


def append_act(db, row, ctx, action, before, evidence=None):
    acts = db.query(CancellationAct).filter_by(company_id=row.company_id, cancellation_id=row.id).order_by(CancellationAct.sequence).all()
    previous, previous_state = None, None
    for sequence, act in enumerate(acts, 1):
        data = json.loads(act.payload)
        if (digest(act.payload) != act.content_hash or canonical(data) != act.payload
                or act.sequence != sequence or data['sequence'] != sequence or data['previous_hash'] != previous
                or data['tenant_id'] != row.company_id or data['cancellation_id'] != row.id or data['event_id'] != act.id
                or data['intent_sha256'] != digest(row.intent) or data['document_sha256'] != row.document_hash
                or (previous_state is not None and data['state_before'] != previous_state)):
            fail('Historial de cancelación alterado')
        previous, previous_state = act.content_hash, data['state_after']
    if previous_state is not None and previous_state != before: fail('Estado de cancelación inconsistente')
    event_id = str(uuid.uuid4())
    payload = canonical({'event_id': event_id, 'tenant_id': row.company_id, 'cancellation_id': row.id,
        'sequence': len(acts)+1, 'actor_id': ctx.user_id, 'action': action, 'state_before': before,
        'state_after': row.status, 'previous_hash': previous, 'document_sha256': row.document_hash,
        'intent_sha256': digest(row.intent), 'recorded_at': datetime.now(timezone.utc).isoformat(),
        'evidence': evidence or {}, 'support_scope': 'application_and_provider_response', 'signature_verified': False})
    db.add(CancellationAct(id=event_id, company_id=row.company_id, cancellation_id=row.id,
        sequence=len(acts)+1, payload=payload, content_hash=digest(payload)))
    db.flush()


def reserve(db, draft, body, ctx, payment_id=None, source=None):
    root, folio, document_hash = source or target(db, draft, payment_id)
    replacement_uuid = None
    if body.replacement_revision_id:
        replacement = db.query(FiscalRevision).filter_by(id=str(body.replacement_revision_id), company_id=draft.company_id,
            draft_id=draft.id, payment_id=payment_id, source_uuid=folio).first()
        if not replacement: fail('Sustitución no encontrada para este comprobante', 404)
        revisions.verify(db, replacement)
        if replacement.status != 'stamped' or not replacement.result_xml or not revisions.validate_result(replacement, replacement.result_xml):
            fail('Primero confirma el XML de la sustitución exacta')
        replacement_uuid = replacement.result_uuid
    if body.replacement_draft_id:
        if payment_id: fail('Sustitución de REP requiere revisión; no se acepta una factura como sustituto')
        replacement = db.query(BillingDraft).filter_by(id=str(body.replacement_draft_id), company_id=draft.company_id).first()
        if not replacement: fail('Factura sustituta no encontrada', 404)
        rroot, replacement_uuid, _ = target(db, replacement)
        assert_invoice_active(db, replacement)
        related = rroot.findall('c:CfdiRelacionados[@TipoRelacion="04"]/c:CfdiRelacionado', NS)
        if replacement_uuid == folio or not any(n.get('UUID', '').lower() == folio for n in related):
            fail('La sustituta debe estar timbrada y relacionar con clave 04 el UUID original')
        if rroot.find('c:Emisor', NS).get('Rfc') != root.find('c:Emisor', NS).get('Rfc') or rroot.find('c:Receptor', NS).get('Rfc') != root.find('c:Receptor', NS).get('Rfc'):
            fail('La sustituta debe pertenecer al mismo emisor y receptor')
    intent = canonical({'reason': body.reason, 'replacement_uuid': replacement_uuid, 'document_uuid': folio})
    existing = find(db, draft, payment_id, lock=True, document_uuid=folio)
    if existing:
        if existing.intent != intent or existing.idempotency_key != str(body.idempotency_key) or existing.document_hash != document_hash:
            fail('Ya existe una solicitud para este comprobante; consulta su estado')
        if existing.status == 'not_sent':
            existing.status = 'processing'
            append_act(db, existing, ctx, 'reserved', 'not_sent')
            return existing, True
        return existing, False
    if not payment_id and db.query(BillingPayment).filter_by(company_id=draft.company_id, draft_id=draft.id).first():
        fail('La factura tiene pagos registrados; resolver sus complementos y aplicaciones antes de cancelarla')
    if payment_id:
        assert_invoice_active(db, draft)
        later = db.query(BillingPayment).join(PaymentReceipt, PaymentReceipt.payment_id == BillingPayment.id).filter(
            BillingPayment.company_id == draft.company_id, BillingPayment.draft_id == draft.id,
            BillingPayment.sequence > db.query(BillingPayment).filter_by(id=payment_id, company_id=draft.company_id).one().sequence,
            PaymentReceipt.status.in_(('stamped', 'processing', 'unknown'))).all()
        if any(not (cancellation := find(db, draft, item.id)) or cancellation.status != 'cancelled' for item in later):
            fail('Hay complementos posteriores; resolverlos antes de cancelar esta parcialidad')
    row = BillingCancellation(id=str(uuid.uuid4()), company_id=draft.company_id, draft_id=draft.id,
        payment_id=payment_id, document_uuid=folio, document_hash=document_hash, idempotency_key=str(body.idempotency_key),
        intent=intent, status='processing')
    db.add(row); db.flush(); append_act(db, row, ctx, 'reserved', 'absent')
    return row, True


def acknowledgement(result, row, emitter_rfc):
    """Even a valid 201 acknowledgement remains requested until status consultation."""
    data = result.get('provider_response')
    if not result.get('ok') or result.get('status_code') != 200 or not isinstance(data, dict): return None
    xml = data.get('XmlAcuse')
    try:
        if not isinstance(xml, str) or len(xml) > 1_000_000 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper(): return None
        root = ET.fromstring(xml)
        if root.tag.split('}')[-1] != 'Acuse' or root.get('RfcEmisor') != emitter_rfc: return None
        matches = [n for n in root.iter() if n.tag.split('}')[-1] == 'UUID']
        if len(matches) != 1 or (matches[0].text or '').lower() != row.document_uuid: return None
        return xml
    except ET.ParseError:
        return None


def sat_status(result, folio):
    data = result.get('provider_response')
    if not result.get('ok') or result.get('status_code') != 200 or not isinstance(data, dict) or str(data.get('Codigo')) != '0': return None
    sat = data.get('RespuestaSat')
    if not isinstance(sat, dict) or str(sat.get('UUID', '')).lower() != folio or not str(sat.get('CodigoEstatusSat', '')).startswith('S -'): return None
    if sat.get('EstadoSat') == 'Cancelado': return 'cancelled'
    if sat.get('EstadoSat') == 'Vigente':
        status = str(sat.get('EstatusCancelacionSat', '')).lower()
        if status == 'solicitud rechazada': return 'rejected'
        if status == 'en proceso': return 'requested'
    return None
