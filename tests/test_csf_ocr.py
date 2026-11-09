import subprocess
from unittest.mock import Mock

import fitz
import pytest

from app.core.config import settings
from app.services import ingest, pdf_text
from app.services.csf_review import build_csf_review
from tests.csf_scan_fixtures import IDENTITY, REGIME, native_pdf, scan_pdf
from tests.test_csf_review import row


@pytest.fixture(autouse=True)
def no_external_services(monkeypatch):
    monkeypatch.setattr(ingest, "_resolve_geolocation", lambda *_: None)
    monkeypatch.setattr(ingest, "_extract_qr_from_pdf", lambda *_: None)
    def forbidden(*args, **kwargs): raise AssertionError("Network, AI and PAC are forbidden")
    monkeypatch.setattr(ingest.requests, "get", forbidden)
    monkeypatch.setattr(ingest.requests, "head", forbidden)
    monkeypatch.setattr(ingest, "_suggest_field_corrections", forbidden)
    monkeypatch.setattr(ingest, "_parse_csf_via_groq", forbidden)


def process(data):
    return ingest.process_pdf(data, "synthetic-tenant", validate_online=False, use_ai=False)


def review(data):
    source = row()
    for key in ("rfc", "razon_social", "regimen", "cp", "id_cif", "crm_autofill", "field_validation"):
        setattr(source, key, data[key])
    return build_csf_review(source)


def test_native_pdf_does_not_call_ocr(monkeypatch):
    monkeypatch.setattr(pdf_text, "_run_tesseract", Mock(side_effect=AssertionError("Native fast path must not call OCR")))
    data = process(native_pdf())
    assert data["rfc"] == "AAA010101AAA"
    assert data["parser_source"] == "regex"
    assert review(data)["extraction"]["method"] == "native"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_actual_image_only_scan_and_right_angle_rotation(rotation):
    data = process(scan_pdf(rotation=rotation))
    result = review(data)
    assert data["rfc"] == "AAA010101AAA"
    assert data["razon_social"] == "EMPRESA SINTETICA DEMO"
    assert data["cp"] == "01234"
    assert result["values"]["fiscalRegime"] == "601"
    assert result["extraction"]["method"] == "ocr"
    assert result["extraction"]["reviewRequired"] is True
    assert data["parser_source"] == "ocr_tesseract"
    assert data["ai_field_corrections"] == []


@pytest.mark.parametrize("options,method", [({"mixed": True}, "mixed"), ({"multiple": True}, "ocr"),
                                           ({"overlay": True}, "ocr"), ({"blur": 1.0}, "ocr")])
def test_mixed_multipage_sparse_overlay_and_mild_blur(options, method):
    data = process(scan_pdf(**options))
    result = review(data)
    assert data["rfc"] == "AAA010101AAA"
    assert result["values"]["fiscalRegime"] == "601"
    assert result["extraction"]["method"] == method


def test_unreadable_scan_is_rejected_instead_of_populating_placeholder_data():
    with pytest.raises(ValueError): process(scan_pdf(unreadable=True))


def test_missing_rfc_is_left_unresolved_for_review():
    data = process(scan_pdf(text_override=(IDENTITY + REGIME).replace("RFC: AAA010101AAA\n", "")))
    result = review(data)
    assert data["rfc"] == "DESCONOCIDO"
    assert "rfc" in result["issues"]


def test_blank_scan_does_not_call_the_engine(monkeypatch):
    monkeypatch.setattr(pdf_text, "_run_tesseract", Mock(side_effect=AssertionError("Blank image needs no engine")))
    with pytest.raises(ValueError, match="OCR_UNREADABLE"):
        process(scan_pdf(text_override=""))


def test_ocr_page_budget_stops_before_processing_an_extra_page(monkeypatch):
    monkeypatch.setattr(settings, "CSF_OCR_MAX_PAGES", 1)
    engine = Mock(return_value=("SYNTHETIC TEXT", [("TEXT", 95)], 0))
    monkeypatch.setattr(pdf_text, "_ocr_page", engine)
    with pytest.raises(ValueError, match="PDF_LIMIT"):
        pdf_text.extract_pdf_text(scan_pdf(multiple=True))
    engine.assert_called_once()


def test_missing_engine_disabled_engine_and_timeout_are_actionable(monkeypatch):
    data = scan_pdf()
    with monkeypatch.context() as patch:
        patch.setattr(pdf_text.shutil, "which", lambda *_: None)
        with pytest.raises(ValueError, match="OCR_UNAVAILABLE"): process(data)
    with monkeypatch.context() as patch:
        patch.setattr(settings, "CSF_OCR_ENABLED", False)
        with pytest.raises(ValueError, match="OCR_UNAVAILABLE"): process(data)
    with monkeypatch.context() as patch:
        patch.setattr(pdf_text.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("tesseract", 1)))
        with pytest.raises(ValueError, match="OCR_TIMEOUT"): process(data)


def test_page_limit_corrupt_and_encrypted_pdf_are_rejected(monkeypatch):
    with pytest.raises(ValueError, match="PDF_INVALID"): process(b"%PDF-broken")
    doc = fitz.open(stream=native_pdf(), filetype="pdf")
    encrypted = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="synthetic-owner", user_pw="synthetic-user")
    doc.close()
    with pytest.raises(ValueError, match="PDF_ENCRYPTED"): process(encrypted)
    monkeypatch.setattr(settings, "CSF_PDF_MAX_PAGES", 1)
    with pytest.raises(ValueError, match="PDF_LIMIT"): process(scan_pdf(multiple=True))


def test_low_word_confidence_marks_only_affected_values_and_changes_revision():
    extraction = pdf_text.PDFText("", 1, pages=[{"page": 1, "rotation": 0}],
        words=[("AAA010101AAA", 20), ("EMPRESA", 98), ("DEMO", 97), ("01234", 99)])
    fields = {"tax_id": "AAA010101AAA", "legal_name": "EMPRESA DEMO", "postal_code": "01234"}
    report = pdf_text.annotate_validation(ingest._build_field_validation_report(fields), extraction)
    source = row(); source.field_validation = report
    result = build_csf_review(source)
    assert "rfc" in result["fieldWarnings"]
    assert "company" not in result["fieldWarnings"]
    assert "zipCode" not in result["fieldWarnings"]
    before = result["revision"]
    source.field_validation[0]["ocr_confidence"] = 30
    assert build_csf_review(source)["revision"] != before
