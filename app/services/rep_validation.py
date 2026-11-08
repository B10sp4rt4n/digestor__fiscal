"""Offline pinned SAT schemas and additional rules for our one-payment MXN profile.
Schema membership is not signature, taxpayer or live catalogue verification.
"""
import gzip
import hashlib
import json
from decimal import Decimal, localcontext, ROUND_DOWN, ROUND_CEILING, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from threading import RLock
from lxml import etree as E

C = 'http://www.sat.gob.mx/cfd/4'
P = 'http://www.sat.gob.mx/Pagos20'
T = 'http://www.sat.gob.mx/TimbreFiscalDigital'
NS = {'c':C, 'p':P, 't':T}
CFDI_URL = 'http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd'
PAGOS_URL = 'http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos20.xsd'
TFD_URL = 'http://www.sat.gob.mx/sitio_internet/cfd/TimbreFiscalDigital/TimbreFiscalDigitalv11.xsd'
LOCK = RLock()


def tax_limits(base_text, rate_text='0.160000', amount_decimals=2):
    """SAT Pagos2.0 pp48–49: truncate lower, ceil upper at ImporteDR scale."""
    base, rate = Decimal(base_text), Decimal(rate_text)
    if not base.is_finite() or not rate.is_finite(): raise ValueError("Invalid tax decimal")
    scale = max(0,-base.as_tuple().exponent)
    if base <= 0 or rate < 0 or scale > 6 or amount_decimals not in range(7):
        raise ValueError('Unsupported tax decimal')
    with localcontext() as ctx:
        ctx.prec=50
        half = Decimal(10) ** -scale / 2
        unit = Decimal(10) ** -amount_decimals
        lower = ((base-half)*rate).quantize(unit,rounding=ROUND_DOWN)
        upper = ((base+half-Decimal('1e-12'))*rate).quantize(unit,rounding=ROUND_CEILING)
        return max(Decimal(0),lower), upper


def tax_in_limits(base_text, amount_text):
    tax=Decimal(amount_text)
    if not tax.is_finite(): return False
    low,high=tax_limits(base_text,amount_decimals=max(0,-tax.as_tuple().exponent))
    return low <= tax <= high


@lru_cache(maxsize=1)
def schemas():
    directory=Path(__file__).resolve().parents[1]/'resources/sat'
    manifest=json.loads((directory/'manifest.json').read_text())
    compressed=(directory/'rep20-schemas.json.gz').read_bytes()
    if hashlib.sha256(compressed).hexdigest()!=manifest['bundle_sha256']: raise ValueError('SAT schema bundle hash mismatch')
    files=json.loads(gzip.decompress(compressed))
    if set(files) != {row['url'] for row in manifest['sources']}: raise ValueError('SAT schema source set mismatch')
    for row in manifest['sources']:
        if hashlib.sha256(files[row['url']].encode()).hexdigest()!=row['sha256']: raise ValueError('SAT schema source hash mismatch')
    class Resolver(E.Resolver):
        def __init__(self,unsigned): self.unsigned=unsigned
        def resolve(self,url,pubid,context):
            url=url.replace('https://','http://',1)
            if url not in files: raise ValueError('External schema resolution forbidden')
            content=files[url]
            if self.unsigned and url==CFDI_URL:
                # Explicit derivative: permits omission of signing fields only.
                root=E.fromstring(content.encode())
                for field in root.xpath('./xs:element/xs:complexType/xs:attribute',namespaces={'xs':'http://www.w3.org/2001/XMLSchema'}):
                    if field.get('name') in {'Sello','Certificado','NoCertificado'}: field.set('use','optional')
                content=E.tostring(root).decode()
            return self.resolve_string(content,context,base_url=url)
    def compile_schema(unsigned):
        parser=E.XMLParser(no_network=True,resolve_entities=False,load_dtd=False)
        parser.resolvers.add(Resolver(unsigned))
        master=f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:import namespace="{C}" schemaLocation="{CFDI_URL}"/><xs:import namespace="{P}" schemaLocation="{PAGOS_URL}"/><xs:import namespace="{T}" schemaLocation="{TFD_URL}"/></xs:schema>'
        return E.XMLSchema(E.fromstring(master.encode(),parser))
    catalogue=E.fromstring(files['http://www.sat.gob.mx/sitio_internet/cfd/catalogos/catCFDI.xsd'].encode())
    postal_codes=frozenset(catalogue.xpath('./xs:simpleType[@name="c_CodigoPostal"]/xs:restriction/xs:enumeration/@value',namespaces={'xs':'http://www.w3.org/2001/XMLSchema'}))
    return compile_schema(False),compile_schema(True),manifest['bundle_sha256'],postal_codes


def validate(xml, *, signed=False):
    errors=[]
    try:
        if len(xml)>5_000_000 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper(): raise ValueError('Unsafe XML')
        root=E.fromstring(xml.encode(),E.XMLParser(no_network=True,resolve_entities=False,load_dtd=False))
        official,unsigned,bundle_hash,postal_codes=schemas()
        with LOCK:
            schema=official if signed else unsigned
            if not schema.validate(root):
                # Do not echo customer names, RFCs or account numbers in errors.
                errors.extend({'rule':'xsd','line':row.line,'type':row.type_name} for row in list(schema.error_log)[:25])
        def require(test,rule):
            if not test: errors.append({'rule':rule})
        require(root.tag=='{'+C+'}Comprobante','cfdi.root')
        require(all(root.get(k)==v for k,v in {'Version':'4.0','TipoDeComprobante':'P','Exportacion':'01','Moneda':'XXX'}.items()),'cfdi.payment_profile')
        require(Decimal(root.get('Total','-1'))==0 and Decimal(root.get('SubTotal','-1'))==0,'cfdi.zero_totals')
        require(not any(k in root.attrib for k in ('FormaPago','MetodoPago','CondicionesDePago','Descuento','TipoCambio')),'cfdi.forbidden_attributes')
        require(root.find('c:Impuestos',NS) is None,'cfdi.no_root_taxes')
        require(all(node.tag in {'{'+C+'}Emisor','{'+C+'}Receptor','{'+C+'}Conceptos','{'+C+'}Complemento'} for node in root),'profile.root_children')
        complement=root.find('c:Complemento',NS)
        if complement is not None:
            require(all(node.tag in {'{'+P+'}Pagos','{'+T+'}TimbreFiscalDigital'} for node in complement),'profile.complement_children')
        customer=root.find('c:Receptor',NS)
        require(customer is not None and customer.get('UsoCFDI')=='CP01','cfdi.payment_use')
        require(customer is not None and customer.get('DomicilioFiscalReceptor') in postal_codes,'catalogue.customer_postal_code')
        concepts=root.findall('c:Conceptos/c:Concepto',NS)
        require(len(concepts)==1,'cfdi.one_concept')
        if len(concepts)==1:
            row=concepts[0]
            require(all(row.get(k)==v for k,v in {'ClaveProdServ':'84111506','Cantidad':'1','ClaveUnidad':'ACT','Descripcion':'Pago','ObjetoImp':'01'}.items()) and Decimal(row.get('ValorUnitario','-1'))==0 and Decimal(row.get('Importe','-1'))==0,'cfdi.payment_concept')
            require(len(row)==0 and not any(k in row.attrib for k in ('NoIdentificacion','Unidad','Descuento')),'cfdi.concept_forbidden')
        pagos=root.findall('c:Complemento/p:Pagos',NS)
        require(len(pagos)==1,'rep.one_complement')
        timbres=root.findall('c:Complemento/t:TimbreFiscalDigital',NS)
        require(len(timbres)==(1 if signed else 0),'cfdi.timbre_count')
        if signed: require(all(root.get(k) for k in ('Sello','NoCertificado','Certificado')),'cfdi.signing_fields_present')
        if len(pagos)==1:
            totals=pagos[0].find('p:Totales',NS); payments=pagos[0].findall('p:Pago',NS)
            require(len(payments)==1,'profile.one_payment')
            for pago in payments:
                require(pago.get('MonedaP')=='MXN' and Decimal(pago.get('TipoCambioP','0'))==1,'profile.mxn_payment')
                require(pago.get('FormaDePagoP') in {'01','02','03','04','28'},'profile.payment_form')
                docs=pago.findall('p:DoctoRelacionado',NS);require(len(docs)==1,'profile.one_document')
                require(pago.get('FechaPago','')<=root.get('Fecha',''),'rep.payment_not_future')
                for doc in docs:
                    require(doc.get('MonedaDR')=='MXN' and Decimal(doc.get('EquivalenciaDR','0'))==1 and doc.get('ObjetoImpDR')=='02','profile.mxn_document')
                    require(Decimal(doc.get('ImpSaldoAnt','-1'))-Decimal(doc.get('ImpPagado','-1'))==Decimal(doc.get('ImpSaldoInsoluto','-2')) and Decimal(doc.get('ImpPagado','0'))>0,'rep.balance_equation')
                    require(Decimal(doc.get('ImpPagado','-1'))==Decimal(pago.get('Monto','-2')),'profile.full_payment_application')
                    transfers=doc.findall('p:ImpuestosDR/p:TrasladosDR/p:TrasladoDR',NS)
                    require(len(transfers)==1 and doc.find('p:ImpuestosDR/p:RetencionesDR',NS) is None,'profile.one_iva_dr')
                    for transfer in transfers:
                        require(all(transfer.get(k)==v for k,v in {'ImpuestoDR':'002','TipoFactorDR':'Tasa','TasaOCuotaDR':'0.160000'}.items()),'profile.iva16_dr')
                        require(tax_in_limits(transfer.attrib['BaseDR'],transfer.attrib['ImporteDR']),'rep.tax_dr_limits')
                    ptrans=pago.findall('p:ImpuestosP/p:TrasladosP/p:TrasladoP',NS)
                    require(len(ptrans)==1 and pago.find('p:ImpuestosP/p:RetencionesP',NS) is None,'profile.one_iva_p')
                    if len(transfers)==len(ptrans)==1:
                        dr,pr=transfers[0],ptrans[0]
                        require(all(pr.get(k)==v for k,v in {'ImpuestoP':'002','TipoFactorP':'Tasa','TasaOCuotaP':'0.160000'}.items()),'profile.iva16_p')
                        require(Decimal(pr.attrib['BaseP'])==Decimal(dr.attrib['BaseDR']) and Decimal(pr.attrib['ImporteP'])==Decimal(dr.attrib['ImporteDR']),'rep.dr_p_sums')
                        if totals is not None:
                            rounding=lambda x:Decimal(x).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                            require(Decimal(totals.get('MontoTotalPagos','-1'))==rounding(pago.attrib['Monto']),'rep.total_payments')
                            require(Decimal(totals.get('TotalTrasladosBaseIVA16','-1'))==rounding(pr.attrib['BaseP']) and Decimal(totals.get('TotalTrasladosImpuestoIVA16','-1'))==rounding(pr.attrib['ImporteP']),'rep.tax_totals')
                            require(set(totals.attrib)=={'MontoTotalPagos','TotalTrasladosBaseIVA16','TotalTrasladosImpuestoIVA16'},'profile.only_iva16_totals')
        return {'valid':not errors,'mode':'signed_schema' if signed else 'unsigned_structure','schema_bundle_sha256':bundle_hash,
            'signature_verified':False,'taxpayer_verified':False,'live_catalogue_verified':False,'errors':errors}
    except (ValueError,KeyError,TypeError,ArithmeticError,E.XMLSyntaxError):
        return {'valid':False,'mode':'signed_schema' if signed else 'unsigned_structure','errors':[{'rule':'invalid_xml_or_profile'}]}
