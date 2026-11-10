"""Real HTTP handlers, PDF parser, queue worker, SQLite storage and review. No network."""
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routers import csf, documents_v1
from app.core.auth import SecurityContext, get_security_context
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.models.csf import CSF
from app.models.document_job import DocumentJob
from app.models.audit_log import AuditLog
import app.models.evento  # register relationship targets
import app.models.sucursal
import app.models.usuario
from app.services import ingest, document_queue as worker
from tests.test_csf_review import pdf
from tests.csf_scan_fixtures import scan_pdf


@pytest.mark.parametrize("scanned", [False, True])
def test_upload_parse_store_review_and_explicit_approval(tmp_path, monkeypatch, scanned):
    for name in ("crm_autofill", "ai_field_corrections", "corrected_json", "field_validation"):
        monkeypatch.setattr(CSF.__table__.c[name], "type", JSON())
    engine = create_engine(f"sqlite:///{tmp_path / 'csf-test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[CSF.__table__, DocumentJob.__table__, AuditLog.__table__])
    session = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", session)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    def no_geolocation(*args): raise AssertionError("CSF review must not wait for geolocation")
    monkeypatch.setattr(ingest, "_resolve_geolocation", no_geolocation)
    monkeypatch.setattr(ingest, "_extract_qr_from_pdf", lambda *_: None)
    def forbidden(*args, **kwargs): raise AssertionError("External network/AI forbidden")
    monkeypatch.setattr(ingest.requests, "get", forbidden)
    monkeypatch.setattr(ingest.requests, "head", forbidden)
    monkeypatch.setattr(ingest, "_suggest_field_corrections", forbidden)
    monkeypatch.setattr(ingest, "_parse_csf_via_groq", forbidden)
    queue = AsyncMock()
    monkeypatch.setattr(documents_v1.document_queue, "enqueue", queue)
    app = FastAPI(); app.include_router(documents_v1.router); app.include_router(csf.router)
    def db():
        with session() as connection: yield connection
    def ctx(request: Request):
        return SecurityContext(request.headers.get("X-Test-Tenant", "tenant-a"), "synthetic-user", "operator")
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_security_context] = ctx
    content = pdf("CONSTANCIA DE SITUACIÓN FISCAL\nRFC: AAA010101AAA\nDenominación/Razón Social: EMPRESA DEMO\nCódigo Postal: 01234\nidCIF: 123456789\nRégimen: General de Ley Personas Morales 01/01/2020")
    if scanned:
        content = scan_pdf(rotation=90)
    with TestClient(app) as client:
        response = client.post("/v1/documents", files={"file": ("csf.pdf", content, "application/pdf")}, data={"document_type": "csf"})
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]
        task = queue.call_args.args[0]
        assert task.use_ai is False
        worker._process_document_task(task)
        result = client.get(f"/v1/documents/{job_id}").json()
        assert result["status"] == "done", result
        document_id = result["document_id"]
        detail = client.get(f"/csf/{document_id}").json()
        review = detail["review"]
        assert review["values"]["company"] == ("EMPRESA SINTETICA DEMO" if scanned else "EMPRESA DEMO")
        assert review["extraction"]["method"] == ("ocr" if scanned else "native")
        assert review["values"]["fiscalRegime"] == "601"
        assert not review["issues"]
        assert client.get(f"/csf/{document_id}/pdf").content == content
        assert client.get(f"/csf/{document_id}", headers={"X-Test-Tenant": "tenant-b"}).status_code == 403
        path = f"/v1/documents/{document_id}/approve"
        assert client.post(path, json={}).status_code == 428
        assert client.post(path, json={"confirm_review": True, "expected_revision": "old"}).status_code == 409
        accepted = client.post(path, json={"confirm_review": True, "expected_revision": review["revision"]})
        assert accepted.status_code == 200, accepted.text
        with session() as connection:
            stored = connection.get(CSF, document_id)
            assert stored.processing_status == "approved_for_sync"
            assert stored.regimen == "601"
            log = connection.query(AuditLog).filter_by(action="document_approved_for_sync").one()
            assert review["revision"] in log.details
    engine.dispose()
