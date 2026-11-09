from types import SimpleNamespace
from unittest.mock import Mock

import fitz
import pytest
from fastapi import HTTPException
from app.core.auth import SecurityContext
from app.core.config import settings
from app.api.routers.documents_v1 import approve_document_for_sync
from app.api.routers import upload
from app.schemas.sync_outbound import DocumentApproveRequest
from app.services import ingest
from app.services.csf_review import build_csf_review


def row(**changes):
    return SimpleNamespace(id="synthetic-csf", company_id="tenant-a", rfc="AAA010101AAA",
        razon_social="EMPRESA DEMO", regimen="General de Ley Personas Morales", cp="01234",
        id_cif="123456789", csf_hash="synthetic-hash", version=1, crm_autofill=None,
        ai_field_corrections=[], corrected_json=None, field_validation=[],
        processing_status="processed", status_reason=None, **changes)


def pdf(text):
    doc = fitz.open(); page = doc.new_page(); page.insert_text((30, 30), text, fontsize=10)
    data = doc.tobytes(); doc.close(); return data


def test_review_normalizes_known_regime_without_ai_and_detects_incompatibility():
    source = row()
    result = build_csf_review(source)
    assert result["values"]["fiscalRegime"] == "601"
    assert not result["issues"]
    source.regimen = "605"
    assert "fiscalRegime" in build_csf_review(source)["issues"]


@pytest.mark.parametrize("field,value,issue", [("rfc", "DESCONOCIDO", "rfc"),
    ("rfc", "XAXX010101000", "rfc"), ("razon_social", "SIN NOMBRE", "company"),
    ("cp", "1234", "zipCode"), ("cp", "00000", "zipCode"),
    ("regimen", "999", "fiscalRegime"), ("id_cif", "PENDIENTE", "cifId")])
def test_placeholder_or_invalid_values_are_not_approvable(field, value, issue):
    source = row(); setattr(source, field, value)
    assert issue in build_csf_review(source)["issues"]


def test_historical_ai_main_value_does_not_replace_original_extraction():
    source = row(); source.razon_social = "NOMBRE CAMBIADO POR IA"
    source.crm_autofill = {"legal_name": "NOMBRE ORIGINAL"}
    source.ai_field_corrections = [{"field": "legal_name", "suggested_value": "OTRO NOMBRE"}]
    before = build_csf_review(source)
    assert before["values"]["company"] == "NOMBRE ORIGINAL"
    source.ai_field_corrections[0]["suggested_value"] = "NUEVA SUGERENCIA"
    assert before["revision"] != build_csf_review(source)["revision"]


def test_pdf_parser_persona_fisica_and_uncaptured_regime_fallback(monkeypatch):
    monkeypatch.setattr(ingest, "_suggest_field_corrections", Mock(side_effect=AssertionError("AI forbidden")))
    monkeypatch.setattr(ingest, "_parse_csf_via_groq", Mock(side_effect=AssertionError("AI forbidden")))
    monkeypatch.setattr(ingest, "_resolve_geolocation", lambda *_: None)
    monkeypatch.setattr(ingest, "_extract_qr_from_pdf", lambda *_: None)
    data = ingest.process_pdf(pdf("CONSTANCIA DE SITUACIÓN FISCAL\nRFC: AAAA010101AAA\nNombre(s): ANA\nPrimer Apellido: DEMO\nSegundo Apellido: PRUEBA\nCódigo Postal: 01234\nRégimen Simplificado de Confianza"), "synthetic", validate_online=False)
    assert data["razon_social"] == "ANA DEMO PRUEBA"
    assert data["regimen"] == "Régimen Simplificado de Confianza"
    assert data["ai_field_corrections"] == []


def test_persistence_keeps_ai_suggestions_separate_from_extracted_main_fields():
    source = row(); db = Mock(); db.query.return_value.filter_by.return_value.first.return_value = source
    source.curp = None; source.source_filename = None; source.extracted_text = None
    source.uploaded_at = None; source.qr_text = None; source.qr_valid = None; source.qr_online = None
    source.parser_source = "regex"
    data = {"company_id": "tenant-a", "csf_hash": "synthetic", "razon_social": "EXTRAIDO",
            "corrected_json": {"legal_name": "CAMBIADO POR IA"}}
    upload._persist_csf(data, db)
    assert source.razon_social == "EXTRAIDO"
    assert data["razon_social"] == "EXTRAIDO"
    assert source.corrected_json == {"legal_name": "CAMBIADO POR IA"}


def approval(source, body, monkeypatch, tenant="tenant-a"):
    db = Mock(); db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = source
    audit = Mock(); monkeypatch.setattr("app.api.routers.documents_v1.audit_service.create_audit_log", audit)
    result = approve_document_for_sync(source.id, body, SecurityContext(tenant, "reviewer", "operator"), db)
    return result, db, audit


def test_approval_requires_explicit_confirmation_and_exact_revision(monkeypatch):
    source = row()
    with pytest.raises(HTTPException) as e: approval(source, DocumentApproveRequest(), monkeypatch)
    assert e.value.status_code == 428
    with pytest.raises(HTTPException) as e:
        approval(source, DocumentApproveRequest(confirm_review=True, expected_revision="old"), monkeypatch)
    assert e.value.status_code == 409
    body = DocumentApproveRequest(confirm_review=True, expected_revision=build_csf_review(source)["revision"])
    result, db, audit = approval(source, body, monkeypatch)
    assert result.status == "approved_for_sync"
    assert audit.call_args.kwargs["details"]["confirmed_review"] is True
    db.commit.assert_called_once()


def test_approval_rejects_invalid_values_and_other_tenant(monkeypatch):
    source = row(); source.rfc = "DESCONOCIDO"
    body = DocumentApproveRequest(confirm_review=True, expected_revision=build_csf_review(source)["revision"])
    with pytest.raises(HTTPException) as e: approval(source, body, monkeypatch)
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e: approval(source, body, monkeypatch, tenant="tenant-b")
    assert e.value.status_code == 403
