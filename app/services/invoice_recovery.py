"""Recover a known provider UUID without issuing another invoice."""
import base64
import json
from datetime import datetime
from xml.etree import ElementTree as ET
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import joinedload
from app.models.commercial import BillingDraft
from app.models.evento import EventoFacturacion
from app.models.fiscal_act import FiscalAct
from app.services import fiscal_acts, timbracfdi_client as pac

CFDI = 'http://www.sat.gob.mx/cfd/4'
TFD = 'http://www.sat.gob.mx/TimbreFiscalDigital'


def business_digest(xml):
    """Full request integrity, allowing only signing/TFD additions by the PAC.
    Conservative lexical comparison can require manual review of harmless
    normalization. This hash does not verify fiscal seals.
    """
    if not isinstance(xml, str) or len(xml) > 5_000_000 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('Unsafe invoice XML')
    root = ET.fromstring(xml)
    if root.tag != '{'+CFDI+'}Comprobante' or root.get('Version') != '4.0' or root.get('TipoDeComprobante') != 'I':
        raise ValueError('Unsupported invoice XML')
    for name in ('Sello', 'Certificado', 'NoCertificado', '{http://www.w3.org/2001/XMLSchema-instance}schemaLocation'):
        root.attrib.pop(name, None)
    for complement in root.findall('{'+CFDI+'}Complemento'):
        for stamp in complement.findall('{'+TFD+'}TimbreFiscalDigital'): complement.remove(stamp)
        if not len(complement) and not complement.attrib and not (complement.text or '').strip(): root.remove(complement)
    def structural(node):
        return [node.tag, sorted(node.attrib.items()), (node.text or '').strip(), [structural(child) for child in node]]
    return fiscal_acts.digest(fiscal_acts.canonical(structural(root)))


def request_evidence(db, draft):
    rows = db.query(FiscalAct).filter_by(company_id=draft.company_id, draft_id=draft.id).order_by(FiscalAct.sequence).all()
    try:
        fiscal_acts.verify(rows)
    except ValueError as exc:
        raise HTTPException(409, 'La evidencia original no es íntegra; requiere revisión manual') from exc
    for row in reversed(rows):
        payload = json.loads(row.payload)
        if payload['action_or_state'] == 'fiscal.request_prepared':
            evidence = payload['evidence_refs']
            if not evidence.get('request_business_sha256') or evidence.get('draft_sha256') != fiscal_acts.draft_digest(draft): break
            return evidence, rows[-1].content_hash
    raise HTTPException(409, 'No existe una solicitud original verificable; requiere revisión manual')


def recover(db, ctx, draft_id, requested_uuid):
    requested_uuid = requested_uuid.lower()
    def assert_unassigned():
        # Historical rows do not have a UUID column; inspect their stored TFD,
        # including inconsistent states with existing XML. Never attach one
        # provider invoice to a second local draft.
        rows = db.query(BillingDraft.stamped_xml_base64).filter(
            BillingDraft.company_id == ctx.tenant_id, BillingDraft.id != draft_id,
            BillingDraft.stamped_xml_base64.isnot(None)).all()
        for (encoded,) in rows:
            try: found = pac.extract_stamp_uuid(base64.b64decode(encoded).decode())
            except (ValueError, UnicodeDecodeError): found = None
            if found and found.lower() == requested_uuid:
                raise HTTPException(409, 'Este UUID ya pertenece a otra prefactura')
    def load(lock=False):
        query = db.query(BillingDraft).options(joinedload(BillingDraft.items)).filter_by(id=draft_id, company_id=ctx.tenant_id)
        if lock: query = query.with_for_update(of=BillingDraft)
        draft = query.one_or_none()
        if not draft: raise HTTPException(404, 'Prefactura no encontrada')
        return draft
    def already(draft):
        if draft.stamp_status == 'stamped' or draft.stamped_xml_base64 or draft.stamped_at:
            try: found = pac.extract_stamp_uuid(base64.b64decode(draft.stamped_xml_base64 or '').decode())
            except (ValueError, UnicodeDecodeError): found = None
            if draft.stamp_status == 'stamped' and found and found.lower() == requested_uuid: return True
            raise HTTPException(409, 'El documento ya tiene evidencia fiscal diferente o incompleta')
        return False
    draft = load()
    if already(draft): return
    if draft.stamp_status != 'unknown':
        raise HTTPException(409, 'Sólo se recuperan resultados desconocidos; un timbrado en proceso permanece protegido')
    evidence, tail = request_evidence(db, draft)
    assert_unassigned()
    emitter = draft.emitter_rfc
    db.rollback()  # No row lock while querying the provider.
    try:
        result = pac.retrieve_cfdi(emitter, requested_uuid)
        info = pac.extract_stamped_document(result)
        xml = info['stamped_xml']
        if not info['success'] or (pac.extract_stamp_uuid(xml) or '').lower() != requested_uuid or business_digest(xml) != evidence['request_business_sha256']:
            raise ValueError('Provider document does not match original request')
    except Exception as exc:
        raise HTTPException(409, 'No se pudo recuperar el XML exacto de la solicitud original; no vuelvas a timbrar') from exc
    db.expire_all()
    if db.bind.dialect.name == 'postgresql':
        key = int(fiscal_acts.digest(ctx.tenant_id+':'+requested_uuid)[:16], 16)
        if key >= 2**63: key -= 2**64
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
    draft = load(lock=True)
    if already(draft): return
    assert_unassigned()
    current, current_tail = request_evidence(db, draft)
    if draft.stamp_status != 'unknown' or current_tail != tail or current != evidence:
        raise HTTPException(409, 'La solicitud cambió durante la recuperación; vuelve a consultar')
    before = fiscal_acts.state(draft)
    draft.stamped_xml_base64 = base64.b64encode(xml.encode()).decode()
    draft.stamp_status = draft.status = 'stamped'
    draft.stamped_at = datetime.utcnow()
    draft.stamped_response_json = json.dumps(result.get('provider_response'), ensure_ascii=False)
    db.add(EventoFacturacion(company_id=draft.company_id, subtotal=draft.subtotal, impuestos=draft.taxes,
        total=draft.total, metodo_pago=draft.payment_method, forma_pago=draft.payment_form,
        evt_hash='draft-recovered:'+draft.id+':'+requested_uuid))
    fiscal_acts.append(db, draft, ctx, 'stamp_recovered', before,
        {**evidence, 'result_xml_sha256': fiscal_acts.digest(xml), 'recovered_uuid': requested_uuid})
    db.commit()
