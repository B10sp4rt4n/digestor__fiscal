"""Restricted MXN/IVA16 REP2 preparation from the original stamped invoice.
No bank verification, automatic PAC calls or claims of SAT certification.
"""
import base64
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET
from fastapi import HTTPException
from app.models.billing_payment import BillingPayment, PaymentReceipt, PaymentAct
from app.services.fiscal_acts import canonical, digest
from app.services.fiscal_rules import money
from app.services.payment_allocation import allocate, VERSION as ALLOCATION_VERSION
from app.services.rep_validation import validate as validate_rep
from app.services.timbracfdi_client import extract_stamp_uuid

CFDI = 'http://www.sat.gob.mx/cfd/4'
PAGOS = 'http://www.sat.gob.mx/Pagos20'
NS = {'c': CFDI, 'p': PAGOS}
LOCAL = ZoneInfo('America/Mexico_City')
SIX = Decimal('0.000001')
RULE = 'digestor.rep20-national-mxn-iva16/2'


def fail(message, status=422):
    raise HTTPException(status_code=status, detail=message)


def invoice_snapshot(draft):
    if draft.stamp_status != 'stamped' or not draft.stamped_xml_base64:
        fail('Se requiere una factura timbrada')
    try:
        xml = base64.b64decode(draft.stamped_xml_base64, validate=True).decode('utf-8')
        if len(xml) > 5_000_000 or '<!DOCTYPE' in xml.upper(): raise ValueError()
        root = ET.fromstring(xml)
        expected = {'Version': '4.0', 'TipoDeComprobante': 'I', 'Moneda': 'MXN', 'MetodoPago': 'PPD', 'FormaPago': '99', 'Exportacion': '01'}
        if root.tag != '{'+CFDI+'}Comprobante' or any(root.get(k) != v for k, v in expected.items()): raise ValueError()
        if root.get('Descuento') or root.find('c:CfdiRelacionados', NS) is not None: raise ValueError()
        emitter, customer = root.find('c:Emisor', NS), root.find('c:Receptor', NS)
        if emitter is None or customer is None: raise ValueError()
        for node, fields in ((emitter, ('Rfc','Nombre','RegimenFiscal')), (customer, ('Rfc','Nombre','DomicilioFiscalReceptor','RegimenFiscalReceptor'))):
            if any(not node.get(k) for k in fields): raise ValueError()
        if emitter.get('Rfc') != draft.emitter_rfc or customer.get('Rfc') != draft.customer_rfc: raise ValueError()
        if customer.get('Rfc') in {'XAXX010101000', 'XEXX010101000'}: raise ValueError()
        total, subtotal = Decimal(root.attrib['Total']), Decimal(root.attrib['SubTotal'])
        taxes = root.find('c:Impuestos', NS)
        groups = root.findall('c:Impuestos/c:Traslados/c:Traslado', NS)
        concepts = root.findall('c:Conceptos/c:Concepto', NS)
        if taxes is None or len(groups) != 1 or not concepts or taxes.find('c:Retenciones', NS) is not None: raise ValueError()
        group = groups[0]
        check = {'Impuesto':'002', 'TipoFactor':'Tasa', 'TasaOCuota':'0.160000'}
        def check_tax(row):
            return all(row.get(k) == v for k,v in check.items())
        if not check_tax(group) or Decimal(group.attrib['Base']) != subtotal: raise ValueError()
        tax = Decimal(group.attrib['Importe'])
        if not all(x.is_finite() and x > 0 for x in (total, subtotal, tax)): raise ValueError()
        if money(total) != total or total != subtotal + tax or total != Decimal(str(draft.total)): raise ValueError()
        if Decimal(taxes.attrib['TotalImpuestosTrasladados']) != tax: raise ValueError()
        bases, amounts = Decimal(0), Decimal(0)
        for concept in concepts:
            rows = concept.findall('c:Impuestos/c:Traslados/c:Traslado', NS)
            if concept.get('ObjetoImp') != '02' or concept.get('Descuento') or len(rows) != 1 or not check_tax(rows[0]): raise ValueError()
            if concept.find('c:Impuestos/c:Retenciones', NS) is not None: raise ValueError()
            b, t = Decimal(rows[0].attrib['Base']), Decimal(rows[0].attrib['Importe'])
            if not b.is_finite() or not t.is_finite() or b <= 0 or t < 0: raise ValueError()
            if Decimal(concept.attrib['Importe']) != b or money(b * Decimal('.16')) != t: raise ValueError()
            bases += b; amounts += t
        if bases != subtotal or amounts != tax: raise ValueError()
        invoice_uuid = extract_stamp_uuid(xml)
        if not invoice_uuid: raise ValueError()
        emitted = datetime.fromisoformat(root.attrib['Fecha'])
        if emitted.tzinfo is None: emitted = emitted.replace(tzinfo=LOCAL)
        return {'xml_sha256': digest(xml), 'uuid': invoice_uuid, 'total': str(total), 'base': str(subtotal), 'tax': str(tax),
                'issued_at': emitted.astimezone(timezone.utc).isoformat(), 'place': root.attrib['LugarExpedicion'],
                'emitter': dict(emitter.attrib), 'customer': dict(customer.attrib)}
    except (ValueError, KeyError, TypeError, ET.ParseError, ArithmeticError):
        fail('El XML original no corresponde al perfil PPD/MXN/IVA16 soportado; requiere revisión')


def declaration(body):
    return {'reference': body.reference, 'amount': f'{body.amount:.2f}',
            'paid_at_utc': body.paid_at.astimezone(timezone.utc).isoformat(), 'payment_form': body.payment_form}


def verify_payments(rows, snapshot):
    balance, base, tax = Decimal(snapshot['total']), Decimal(0), Decimal(0)
    for sequence, row in enumerate(rows, 1):
        try:
            data = json.loads(row.declaration)
            if (row.sequence != sequence or digest(row.declaration) != row.declaration_hash or canonical(data) != row.declaration
                or row.invoice_hash != snapshot['xml_sha256'] or row.balance_before != balance or row.amount <= 0
                or row.balance_after != balance - row.amount or row.balance_after < 0
                or data['amount'] != f'{row.amount:.2f}' or data['reference'] != row.reference
                or data['payment_form'] != row.payment_form
                or datetime.fromisoformat(data['paid_at_utc']).replace(tzinfo=None) != row.paid_at
                or data['invoice_snapshot'] != snapshot or data['sequence'] != sequence
                or data['payment_id'] != row.id or data['tenant_id'] != row.company_id
                or data['draft_id'] != row.draft_id or data['idempotency_key'] != row.idempotency_key
                or data['balance_before'] != f'{row.balance_before:.2f}' or data['balance_after'] != f'{row.balance_after:.2f}'
                or data['tax_base'] != f'{row.tax_base:.6f}' or data['tax_amount'] != f'{row.tax_amount:.6f}'):
                raise ValueError()
            balance = row.balance_after; base += row.tax_base; tax += row.tax_amount
        except (ValueError, KeyError, TypeError):
            fail('Integridad del historial de pagos no verificable', 409)
    return balance, base, tax


def append_act(db, payment, receipt, ctx, action, before, evidence=None):
    transitions = {'registered': ({'absent'}, {'not_sent'}), 'reserved': ({'not_sent','rejected'}, {'processing'}),
        'prepared': ({'processing'}, {'processing'}), 'not_called': ({'processing'}, {'not_sent'}),
        'unknown': ({'processing'}, {'unknown'}), 'confirmed': ({'processing'}, {'stamped'}), 'rejected': ({'processing'}, {'rejected'})}
    old, new = transitions[action]
    if before not in old or receipt.status not in new: fail('Transición de complemento no permitida', 409)
    rows = db.query(PaymentAct).filter_by(company_id=payment.company_id, payment_id=payment.id).order_by(PaymentAct.sequence).all()
    previous, previous_id, previous_state = None, None, None
    for index, row in enumerate(rows, 1):
        try:
            data = json.loads(row.payload)
            if (row.sequence != index or row.previous_hash != previous or digest(row.payload) != row.content_hash
                or canonical(data) != row.payload or data['event_id'] != row.id or data['sequence'] != index
                or data['tenant_id'] != payment.company_id or data['subject']['entity_id'] != payment.id
                or data['previous_hash'] != previous or data['previous_event_id'] != previous_id
                or (previous_state is not None and data['state_before'] != previous_state)):
                raise ValueError()
            previous, previous_id, previous_state = row.content_hash, row.id, data['state_after']
        except (ValueError, KeyError, TypeError): fail('Integridad de actos de pago no verificable', 409)
    if previous_state is not None and previous_state != before: fail('Estado de complemento no coincide con su historial', 409)
    now = datetime.now(timezone.utc); event_id = str(uuid.uuid4())
    payload = {'recordia_version':'1.0', 'contract_version':'payment-act/1.0', 'event_id':event_id,
        'case_id':f'digestor:{payment.company_id}:payment:{payment.id}', 'sequence':len(rows)+1,
        'tenant_id':payment.company_id, 'subject':{'entity_type':'billing_payment','entity_id':payment.id},
        'actors':[{'actor_id':ctx.user_id,'role':ctx.role,'identity_status':'declared'}],
        'action_or_state':'payment.'+action, 'state_before':before, 'state_after':receipt.status,
        'epistemic_class':'recorded_claim' if action == 'registered' else 'observed_fact',
        'support_status':'supported', 'support_scope':'operator_declaration' if action == 'registered' else 'application_transition_only',
        'occurred_at_utc': now.isoformat(), 'recorded_at_utc':now.isoformat(),
        'declared_payment_at':json.loads(payment.declaration)['paid_at_original'],
        'source_refs':['billing_payment:'+payment.id, 'billing_draft:'+payment.draft_id],
        'evidence_refs':{'declaration_sha256':payment.declaration_hash, **(evidence or {})},
        'policy_or_rule_refs':[RULE], 'previous_hash':previous, 'previous_event_id':previous_id,
        'relations':[{'predicate':'follows','target_id':previous_id}] if previous_id else [],
        'memory':{'status':'local_only','custody_status':'not_requested'}, 'bank_verification':'not_performed'}
    encoded = canonical(payload)
    db.add(PaymentAct(id=event_id, company_id=payment.company_id, payment_id=payment.id, sequence=len(rows)+1,
        payload=encoded, content_hash=digest(encoded), previous_hash=previous, recorded_at=now.replace(tzinfo=None)))
    db.flush()


def register(db, draft, body, ctx):
    snap = invoice_snapshot(draft)
    rows = db.query(BillingPayment).filter_by(company_id=draft.company_id,draft_id=draft.id).order_by(BillingPayment.sequence).all()
    balance, allocated_base, allocated_tax = verify_payments(rows, snap)
    normalized = declaration(body)
    for row in rows:
        if row.idempotency_key == str(body.idempotency_key):
            original = json.loads(row.declaration)
            if any(original[k] != v for k,v in normalized.items()): fail('La clave de idempotencia ya tiene otros datos', 409)
            return row
        if row.reference == body.reference: fail('La referencia de pago ya está registrada en esta factura', 409)
    duplicate = db.query(BillingPayment).filter(BillingPayment.company_id==draft.company_id,
        (BillingPayment.reference==body.reference) | (BillingPayment.idempotency_key==str(body.idempotency_key))).first()
    if duplicate is not None: fail('La referencia o clave ya corresponde a otro pago de esta empresa', 409)
    now = datetime.now(timezone.utc)
    if body.paid_at > now: fail('No se permiten pagos a futuro')
    if body.paid_at < datetime.fromisoformat(snap['issued_at']): fail('El pago antecede a la factura; anticipos fuera de alcance')
    if rows and body.paid_at.astimezone(timezone.utc).replace(tzinfo=None) < rows[-1].paid_at: fail('Registra los pagos en orden cronológico')
    if len(rows) >= 999: fail('Número de parcialidades fuera del perfil soportado')
    if body.amount > balance: fail('El pago excede el saldo pendiente', 409)
    remaining = balance - body.amount
    try:
        base,tax=allocate(snap,body.amount,balance,allocated_base,allocated_tax)
    except ValueError as exc:
        fail(str(exc))
    payment_id = str(uuid.uuid4())
    normalized.update({'allocation_version':ALLOCATION_VERSION, 'paid_at_original':body.paid_at.isoformat(), 'invoice_snapshot':snap,
        'payment_id':payment_id, 'tenant_id':draft.company_id,'draft_id':draft.id,'idempotency_key':str(body.idempotency_key),
        'sequence':len(rows)+1, 'balance_before':f'{balance:.2f}', 'balance_after':f'{remaining:.2f}',
        'tax_base':f'{base:.6f}', 'tax_amount':f'{tax:.6f}'})
    encoded = canonical(normalized)
    payment = BillingPayment(id=payment_id, company_id=draft.company_id,draft_id=draft.id,sequence=len(rows)+1,
        idempotency_key=str(body.idempotency_key),reference=body.reference,paid_at=body.paid_at.astimezone(timezone.utc).replace(tzinfo=None),
        payment_form=body.payment_form,amount=body.amount,balance_before=balance,balance_after=remaining,
        tax_base=base,tax_amount=tax,invoice_hash=snap['xml_sha256'],declaration=encoded,declaration_hash=digest(encoded))
    db.add(payment); db.flush()
    receipt = PaymentReceipt(company_id=draft.company_id,payment_id=payment.id,status='not_sent')
    db.add(receipt); db.flush()
    append_act(db,payment,receipt,ctx,'registered','absent')
    return payment


def build_xml(payment, *, now=None):
    data = json.loads(payment.declaration); snap = data['invoice_snapshot']
    if digest(payment.declaration) != payment.declaration_hash: fail('Declaración de pago alterada',409)
    now = now or datetime.now(timezone.utc)
    tax_text = f'{payment.tax_amount:.2f}' if data.get('allocation_version') == ALLOCATION_VERSION else f'{payment.tax_amount:.6f}'
    root = ET.Element('{'+CFDI+'}Comprobante', {'Version':'4.0','Serie':'RP','Folio':payment.id,
        'Fecha':now.astimezone(LOCAL).strftime('%Y-%m-%dT%H:%M:%S'), 'SubTotal':'0','Moneda':'XXX','Total':'0',
        'TipoDeComprobante':'P','Exportacion':'01','LugarExpedicion':snap['place'],
        '{http://www.w3.org/2001/XMLSchema-instance}schemaLocation':CFDI+' http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd '+PAGOS+' http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos20.xsd'})
    ET.SubElement(root,'{'+CFDI+'}Emisor', {k:snap['emitter'][k] for k in ('Rfc','Nombre','RegimenFiscal')})
    receptor = {k:snap['customer'][k] for k in ('Rfc','Nombre','DomicilioFiscalReceptor','RegimenFiscalReceptor')}
    receptor['UsoCFDI'] = 'CP01'; ET.SubElement(root,'{'+CFDI+'}Receptor',receptor)
    concepts = ET.SubElement(root,'{'+CFDI+'}Conceptos')
    ET.SubElement(concepts,'{'+CFDI+'}Concepto', {'ClaveProdServ':'84111506','Cantidad':'1','ClaveUnidad':'ACT','Descripcion':'Pago','ValorUnitario':'0','Importe':'0','ObjetoImp':'01'})
    pagos = ET.SubElement(ET.SubElement(root,'{'+CFDI+'}Complemento'),'{'+PAGOS+'}Pagos',{'Version':'2.0'})
    ET.SubElement(pagos,'{'+PAGOS+'}Totales',{'MontoTotalPagos':f'{payment.amount:.2f}',
        'TotalTrasladosBaseIVA16':f'{money(payment.tax_base):.2f}','TotalTrasladosImpuestoIVA16':f'{money(payment.tax_amount):.2f}'})
    pago = ET.SubElement(pagos,'{'+PAGOS+'}Pago',{'FechaPago':payment.paid_at.replace(tzinfo=timezone.utc).astimezone(LOCAL).strftime('%Y-%m-%dT%H:%M:%S'),
        'FormaDePagoP':payment.payment_form,'MonedaP':'MXN','TipoCambioP':'1','Monto':f'{payment.amount:.2f}', 'NumOperacion':payment.reference})
    doc = ET.SubElement(pago,'{'+PAGOS+'}DoctoRelacionado',{'IdDocumento':snap['uuid'],'MonedaDR':'MXN','EquivalenciaDR':'1',
        'NumParcialidad':str(payment.sequence),'ImpSaldoAnt':f'{payment.balance_before:.2f}','ImpPagado':f'{payment.amount:.2f}',
        'ImpSaldoInsoluto':f'{payment.balance_after:.2f}','ObjetoImpDR':'02'})
    dr = ET.SubElement(ET.SubElement(doc,'{'+PAGOS+'}ImpuestosDR'),'{'+PAGOS+'}TrasladosDR')
    ET.SubElement(dr,'{'+PAGOS+'}TrasladoDR',{'BaseDR':f'{payment.tax_base:.6f}','ImpuestoDR':'002','TipoFactorDR':'Tasa', 'TasaOCuotaDR':'0.160000','ImporteDR':tax_text})
    pr = ET.SubElement(ET.SubElement(pago,'{'+PAGOS+'}ImpuestosP'),'{'+PAGOS+'}TrasladosP')
    ET.SubElement(pr,'{'+PAGOS+'}TrasladoP',{'BaseP':f'{payment.tax_base:.6f}','ImpuestoP':'002','TipoFactorP':'Tasa', 'TasaOCuotaP':'0.160000','ImporteP':tax_text})
    ET.register_namespace('cfdi',CFDI); ET.register_namespace('pago20',PAGOS); ET.register_namespace('xsi','http://www.w3.org/2001/XMLSchema-instance')
    return ET.tostring(root, encoding='unicode', xml_declaration=True)


def validate_returned_xml(request_xml, stamped_xml):
    """A UUID alone is insufficient: bind the returned REP business data."""
    try:
        if len(stamped_xml) > 5_000_000 or '<!DOCTYPE' in stamped_xml.upper(): return False
        expected, actual = ET.fromstring(request_xml), ET.fromstring(stamped_xml)
        def structural(node):
            return (node.tag, tuple(sorted(node.attrib.items())), tuple(structural(child) for child in node))
        for field in ('Version','TipoDeComprobante','Moneda','Total','SubTotal','Fecha','Folio','Serie','Exportacion','LugarExpedicion'):
            if expected.get(field) != actual.get(field): return False
        for path in ('c:Emisor','c:Receptor','c:Conceptos','c:Complemento/p:Pagos'):
            a,b = expected.find(path,NS),actual.find(path,NS)
            if a is None or b is None or structural(a) != structural(b): return False
        return actual.tag == expected.tag and extract_stamp_uuid(stamped_xml) is not None and validate_rep(stamped_xml, signed=True)['valid']
    except (ValueError, TypeError, ET.ParseError): return False
