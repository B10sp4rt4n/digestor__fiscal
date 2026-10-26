"""Pinned schemas, independent SAT boundary examples, and conservation properties."""
import base64
from decimal import Decimal, ROUND_HALF_UP
from random import Random
from xml.etree import ElementTree as ET
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from app.services import rep_validation as validation, payment_receipts as svc
from app.services.payment_allocation import allocate
from test_payment_cycle import db, no_network, seed, body, record, accepted, CTX, api


def preliminary(db):
    seed(db); payment=record(db)
    row=db.query(svc.BillingPayment).filter_by(id=payment['id']).one()
    return svc.build_xml(row),payment


def test_sat_documented_lower_upper_depends_on_lexical_precision():
    # Base=1, tax16%; pp48–49 formulas: ±0.5 at scale0, ±0.0000005 at scale6.
    assert validation.tax_limits('1',amount_decimals=2)==(Decimal('.08'),Decimal('.24'))
    assert validation.tax_limits('1.000000',amount_decimals=2)==(Decimal('.15'),Decimal('.17'))
    assert validation.tax_limits('1.000000',amount_decimals=6)==(Decimal('.159999'),Decimal('.160001'))
    assert validation.tax_in_limits('100.010000','16.00')
    assert not validation.tax_in_limits('100.010000','16.000000')


@pytest.mark.parametrize('value',['NaN','Infinity','0','-1','1.0000001'])
def test_invalid_tax_bounds_fail(value):
    with pytest.raises(ValueError):validation.tax_limits(value)


def test_official_signed_schema_is_not_reported_as_unsigned_success(db):
    xml,_=preliminary(db)
    report=validation.validate(xml)
    assert report['valid'] and report['mode']=='unsigned_structure'
    assert not report['signature_verified'] and not report['live_catalogue_verified']
    report=validation.validate(xml,signed=True)
    assert not report['valid']
    result=accepted(xml_base64=base64.b64encode(xml.encode()).decode())
    assert validation.validate(result['provider_response']['Xml'],signed=True)['valid']
    # Fixture carries fake signing fields; structural validity is not cryptographic proof.
    assert not validation.validate(result['provider_response']['Xml'],signed=True)['signature_verified']


@pytest.mark.parametrize('target,attr,value',[
    ('.','MetodoPago','PUE'), ('.','Moneda','MXN'), ('.','Total','1'),
    ('c:Receptor','UsoCFDI','G03'), ('c:Receptor','DomicilioFiscalReceptor','00001'),
    ('c:Conceptos/c:Concepto','ClaveProdServ','00000000'),
    ('c:Conceptos/c:Concepto','ClaveUnidad','ZZZ'),
    ('c:Complemento/p:Pagos/p:Pago','FormaDePagoP','99'),
    ('c:Complemento/p:Pagos/p:Pago/p:DoctoRelacionado','ImpSaldoInsoluto','57'),
    ('c:Complemento/p:Pagos/p:Pago/p:DoctoRelacionado/p:ImpuestosDR/p:TrasladosDR/p:TrasladoDR','ImporteDR','7.99'),
    ('c:Complemento/p:Pagos/p:Pago/p:ImpuestosP/p:TrasladosP/p:TrasladoP','BaseP','49'),
    ('c:Complemento/p:Pagos/p:Totales','MontoTotalPagos','57'),
])
def test_schema_or_additional_rules_detect_invalid_mutation(db,target,attr,value):
    xml,_=preliminary(db);root=ET.fromstring(xml)
    row=root if target=='.' else root.find(target,svc.NS);row.set(attr,value)
    assert not validation.validate(ET.tostring(root,encoding='unicode'))['valid']


def test_external_entities_unknown_complements_and_required_nodes_fail(db):
    xml,_=preliminary(db)
    assert not validation.validate('<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]>'+xml)['valid']
    root=ET.fromstring(xml);comp=root.find('c:Complemento',svc.NS)
    ET.SubElement(comp,'{urn:unapproved}Other')
    assert not validation.validate(ET.tostring(root,encoding='unicode'))['valid']
    root=ET.fromstring(xml);pagos=root.find('c:Complemento/p:Pagos',svc.NS)
    pagos.remove(pagos.find('p:Totales',svc.NS))
    assert not validation.validate(ET.tostring(root,encoding='unicode'))['valid']


def test_preflight_failure_does_not_reserve_or_contact_provider(db,monkeypatch):
    _,payment=preliminary(db);original=svc.build_xml
    monkeypatch.setattr(svc,'build_xml',lambda *a,**k:original(*a,**k).replace('ClaveUnidad="ACT"','ClaveUnidad="ZZZ"'))
    pac=Mock();monkeypatch.setattr(api.pac,'timbra_cfdi',pac)
    with pytest.raises(HTTPException) as exc:api.stamp_receipt('credit-invoice',payment['id'],CTX,db)
    assert exc.value.status_code==422;pac.assert_not_called()
    assert api.read_receipt('credit-invoice',payment['id'],CTX,db)['receipt_status']=='not_sent'


def test_rounding_remainder_is_valid_after_every_payment():
    random=Random(20261026)
    bases=[Decimal('.04'),Decimal('.07'),Decimal('1.01'),Decimal('100.01'),Decimal('999999999999.00')]
    bases += [Decimal(random.randint(4,10000000))/100 for _ in range(400)]
    for original_base in bases:
        original_tax=(original_base*Decimal('.16')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        total=original_base+original_tax
        snap={'base':str(original_base),'tax':str(original_tax),'total':str(total)}
        balance=total;base_sum=Decimal(0);tax_sum=Decimal(0)
        for index in range(50):
            if balance<=0:break
            cents=int(balance*100)
            amount=balance if index==49 else Decimal(random.randint(1,max(1,cents)))/100
            b,t=allocate(snap,amount,balance,base_sum,tax_sum)
            assert validation.tax_in_limits(f'{b:.6f}',f'{t:.2f}')
            balance-=amount;base_sum+=b;tax_sum+=t
            if balance:
                final_b,final_t=allocate(snap,balance,balance,base_sum,tax_sum)
                assert final_b+base_sum==original_base and final_t+tax_sum==original_tax
                assert validation.tax_in_limits(f'{final_b:.6f}',f'{final_t:.2f}')
        assert balance==0 and base_sum==original_base and tax_sum==original_tax


def test_many_cent_payments_preserve_base_and_tax():
    snap={'base':'100.01','tax':'16.00','total':'116.01'}
    balance=Decimal(snap['total']);base=Decimal(0);tax=Decimal(0)
    for index in range(999):
        amount=Decimal('.01') if index<998 else balance
        b,t=allocate(snap,amount,balance,base,tax)
        assert validation.tax_in_limits(f'{b:.6f}',f'{t:.2f}')
        balance-=amount;base+=b;tax+=t
    assert balance==0 and base==Decimal('100.01') and tax==Decimal('16.00')


def test_unsupported_old_fractional_tax_is_not_silently_rounded():
    with pytest.raises(ValueError,match='historial'):
        allocate({'base':'100','tax':'16','total':'116'},Decimal('10'),Decimal('115.99'),Decimal('.008621'),Decimal('.001379'))


def test_unrepresentable_multi_line_tax_cannot_be_registered():
    with pytest.raises(ValueError):
        allocate({'base':'100.75','tax':'16','total':'116.75'},Decimal('58'),Decimal('116.75'),Decimal(0),Decimal(0))


def test_sandbox_label_cannot_route_ppd_to_production_endpoint(db,monkeypatch):
    from app.core.fiscal_sandbox import ppd_transport_is_sandbox
    seed(db)
    assert ppd_transport_is_sandbox()
    monkeypatch.setattr(api.settings,'TIMBRACFDI_USE_SOAP',True)
    monkeypatch.setattr(api.settings,'TIMBRACFDI_SOAP_URL','https://timbracfdi33.mx:1443/Timbrado.asmx')
    assert not ppd_transport_is_sandbox()
    with pytest.raises(HTTPException) as exc:record(db)
    assert exc.value.status_code==503
    monkeypatch.setattr(api.settings,'TIMBRACFDI_USE_SOAP',False)
    monkeypatch.setattr(api.settings,'TIMBRACFDI_SANDBOX_BASE_URL','https://timbracfdi33.mx:1443/api')
    assert not ppd_transport_is_sandbox()


def test_invalid_signed_return_cannot_be_saved_as_issued(db,monkeypatch):
    _,payment=preliminary(db)
    def unsigned_result(**kwargs):
        result=accepted(**kwargs)
        root=ET.fromstring(result['provider_response']['Xml']); del root.attrib['Certificado']
        result['provider_response']['Xml']=ET.tostring(root,encoding='unicode');return result
    monkeypatch.setattr(api.pac,'timbra_cfdi',Mock(side_effect=unsigned_result))
    with pytest.raises(HTTPException) as exc:api.stamp_receipt('credit-invoice',payment['id'],CTX,db)
    assert exc.value.status_code==502
    assert api.read_receipt('credit-invoice',payment['id'],CTX,db)['receipt_status']=='unknown'
