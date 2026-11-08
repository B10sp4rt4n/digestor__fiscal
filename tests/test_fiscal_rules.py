from decimal import Decimal
from datetime import datetime
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app.api.routers import commercial_v1 as api
from app.schemas.commercial import DraftItemInput
from app.models.commercial import BillingDraft


def draft(**changes):
    item_data = {"description": "Synthetic service", "quantity": 1, "unit_price": 100,
                 "sat_product_code": "80101500", "unit_code": "E48", "tax_rate": 0.16,
                 **changes.pop("item", {})}
    item = api._resolve_item_payload(Mock(), "synthetic", DraftItemInput(**item_data))
    item.id = "synthetic-item"
    result = BillingDraft(stamp_status="not_sent", created_at=datetime.utcnow(), id="synthetic", company_id="synthetic", customer_name="SYNTHETIC CUSTOMER",
        customer_rfc="AAA010101AAA", customer_zip="01000", customer_regimen="601", customer_use_cfdi="G03",
        emitter_rfc="EKU9003173C9", emitter_name="SYNTHETIC EMITTER", emitter_regimen="601", place_of_issue="01000",
        currency="MXN", payment_method="PUE", payment_form="03", items=[item])
    for key, value in changes.items(): setattr(result, key, value)
    api._recalculate_draft(result)
    return result


@pytest.mark.parametrize("quantity,price", [(1.234, 100), (100, 1.234), (0.123456, 1.234567), (3, 0.015)])
def test_xml_units_match_decimal_calculation(quantity, price):
    root = ET.fromstring(api._build_cfdi_xml(draft(item={"quantity": quantity, "unit_price": price})))
    concept = root.find("{http://www.sat.gob.mx/cfd/4}Conceptos/{http://www.sat.gob.mx/cfd/4}Concepto")
    from app.services.fiscal_rules import money
    assert money(Decimal(concept.get("Cantidad")) * Decimal(concept.get("ValorUnitario"))) == Decimal(concept.get("Importe"))
    assert Decimal(concept.get("Cantidad")) == Decimal(str(quantity))
    assert Decimal(concept.get("ValorUnitario")) == Decimal(str(price))


@pytest.mark.parametrize("changes,reason", [
    ({"currency": "USD"}, "unsupported_currency"),
    ({"payment_method": "PPD", "payment_form": "99"}, "unsupported_payment_method"),
    ({"payment_form": "99"}, "unsupported_payment_form"),
    ({"customer_rfc": "BAD"}, "customer_rfc_syntax"),
    ({"customer_rfc": "XEXX010101000"}, "unsupported_generic_rfc"),
    ({"customer_rfc": "XAXX010101000"}, "unsupported_generic_rfc"),
    ({"customer_zip": "X"}, "customer_zip_syntax"),
    ({"item": {"sat_product_code": ""}}, "items.0.sat_product_code"),
    ({"item": {"unit_code": ""}}, "items.0.unit_code"),
    ({"item": {"tax_rate": 0}}, "items.0.unsupported_tax_profile"),
    ({"item": {"tax_object": "01"}}, "items.0.unsupported_tax_profile"),
])
def test_unsupported_scenarios_do_not_appear_ready(changes, reason):
    value = draft(**changes)
    assert reason in api._missing_fields(value)
    assert api._serialize_draft(value).ready_to_stamp is False
    with pytest.raises(HTTPException) as error: api._build_cfdi_xml(value)
    assert error.value.status_code == 422


@pytest.mark.parametrize("field,value", [("quantity", "1.0000001"), ("unit_price", "0.1234567"),
    ("unit_price", float("inf")), ("quantity", float("nan")), ("quantity", -1), ("unit_price", 100000000), ("quantity", "1e100")])
def test_rejects_precision_or_values_that_legacy_storage_cannot_preserve(field, value):
    with pytest.raises(ValidationError): DraftItemInput(**{field: value})


def test_mismatched_stored_totals_cannot_be_stamped():
    value = draft()
    value.total = 999
    assert "draft_amount_mismatch" in api._missing_fields(value)
    with pytest.raises(HTTPException): api._build_cfdi_xml(value)


def test_aggregate_amount_limit_is_validation_error():
    with pytest.raises(HTTPException) as error:
        draft(item={"quantity": 99999999, "unit_price": 99999999})
    assert error.value.status_code == 422


def test_small_tax_keeps_explicit_tax_profile_in_xml():
    root = ET.fromstring(api._build_cfdi_xml(draft(item={"unit_price": 0.01})))
    transfers = root.findall('.//{http://www.sat.gob.mx/cfd/4}Traslado')
    assert len(transfers) == 2
    assert all(row.get('TasaOCuota') == '0.160000' for row in transfers)
