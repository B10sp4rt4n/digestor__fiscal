"""Offline PAC fixtures; synthetic XML, never a real invoice or seal."""
import html
from unittest.mock import Mock

import pytest
from app.services import timbracfdi_client as pac
from test_billing_stamp_integration import STAMPED_XML


def soap(body, status=200):
    return pac._normalize_soap_response(Mock(text=body, status_code=status, ok=status < 400))


@pytest.mark.parametrize("escaped", [False, True])
def test_soap_success_supports_direct_and_escaped_values(escaped):
    values = ["0", STAMPED_XML]
    if escaped:
        values = [html.escape('<anyType xsi:type="xsd:string">' + v) for v in values]
    else:
        values = [html.escape(v) for v in values]
    response = soap("<soap:Body>" + "".join('<anyType xsi:type="xsd:string">' + v + '</anyType>' for v in values) + "</soap:Body>")
    extracted = pac.extract_stamped_document(response)
    assert extracted["success"]
    assert pac.extract_stamp_uuid(extracted["stamped_xml"]) == "11111111-2222-4333-8444-555555555555"


def test_soap_explicit_rejection_and_transport_failure_are_distinct():
    body = '<anyType>Validacion</anyType><anyType>CFDI40139</anyType><anyType>Synthetic rejection</anyType>'
    assert pac.is_explicit_stamp_rejection(soap(body))
    assert not pac.is_explicit_stamp_rejection(soap(body, 503))
    assert soap(body, 503)["status_code"] == 503
    assert not pac.is_explicit_stamp_rejection(soap('<soap:Fault>Unknown</soap:Fault>'))


@pytest.mark.parametrize("xml", [
    "TimbreFiscalDigital UUID=\"11111111-2222-4333-8444-555555555555\"",
    '<root><TimbreFiscalDigital UUID="11111111-2222-4333-8444-555555555555"/></root>',
    STAMPED_XML.replace('http://www.sat.gob.mx/TimbreFiscalDigital', 'urn:wrong'),
    STAMPED_XML.replace('11111111-2222-4333-8444-555555555555', 'not-a-uuid'),
    STAMPED_XML.replace('</cfdi:Complemento>', '<tfd:TimbreFiscalDigital UUID="11111111-2222-4333-8444-555555555555"/></cfdi:Complemento>'),
])
def test_invalid_structure_is_not_a_confirmed_stamp(xml):
    assert pac.extract_stamp_uuid(xml) is None


def test_single_quoted_uuid_is_supported():
    assert pac.extract_stamp_uuid(STAMPED_XML.replace('UUID="11111111-2222-4333-8444-555555555555"', "UUID='11111111-2222-4333-8444-555555555555'"))
