"""
Router de carga de CSFs — /upload/pdf y /upload/zip
"""
from datetime import datetime
import time
import uuid
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard
from app.core.config import settings
from app.db.session import get_db
from app.models.csf import CSF
from app.schemas.csf import UploadResult
from app.services import ingest
from app.services import telemetry

router = APIRouter(prefix="/upload", tags=["upload"])


def _compute_processing_status(qr_valid: bool | None, source_filename: str | None) -> tuple[str, str | None]:
    if qr_valid is True:
        return "processed", None
    if qr_valid is False:
        return "needs_review", "qr_invalid"
    if source_filename:
        return "pending_qr", "qr_not_detected"
    return "incomplete", "missing_source_file"


def _persist_csf(data: dict, db: Session) -> tuple[CSF, bool]:
    """Inserta o recupera CSF por hash. Retorna (csf, created)."""
    existing = db.query(CSF).filter_by(csf_hash=data["csf_hash"]).first()
    if existing:
        updated = False
        if data.get("razon_social") and existing.razon_social != data["razon_social"]:
            existing.razon_social = data["razon_social"]
            updated = True
        if data.get("regimen") and existing.regimen != data["regimen"]:
            existing.regimen = data["regimen"]
            updated = True
        if data.get("cp") and existing.cp != data["cp"]:
            existing.cp = data["cp"]
            updated = True
        if data.get("curp") and existing.curp != data["curp"]:
            existing.curp = data["curp"]
            updated = True
        if data.get("id_cif") and existing.id_cif != data["id_cif"]:
            existing.id_cif = data["id_cif"]
            updated = True
        if data.get("source_filename") and existing.source_filename != data["source_filename"]:
            existing.source_filename = data["source_filename"]
            updated = True
        if data.get("extracted_text") and existing.extracted_text != data["extracted_text"]:
            existing.extracted_text = data["extracted_text"]
            updated = True
        if existing.uploaded_at is None:
            existing.uploaded_at = datetime.utcnow()
            updated = True
        if data.get("qr_text") and existing.qr_text != data["qr_text"]:
            existing.qr_text = data["qr_text"]
            updated = True
        if data.get("qr_valid") is not None and existing.qr_valid != data["qr_valid"]:
            existing.qr_valid = data["qr_valid"]
            updated = True
        if data.get("qr_online") is not None and existing.qr_online != data["qr_online"]:
            existing.qr_online = data["qr_online"]
            updated = True
        if data.get("parser_source") and existing.parser_source != data["parser_source"]:
            existing.parser_source = data["parser_source"]
            updated = True
        status, reason = _compute_processing_status(existing.qr_valid, existing.source_filename)
        if not updated:
            status = "duplicate"
            reason = "same_hash"
        if existing.processing_status != status:
            existing.processing_status = status
            updated = True
        if existing.status_reason != reason:
            existing.status_reason = reason
            updated = True
        if updated:
            db.commit()
            db.refresh(existing)
        return existing, False

    status, reason = _compute_processing_status(data.get("qr_valid"), data.get("source_filename"))

    csf = CSF(
        id=str(uuid.uuid4()),
        company_id=data["company_id"],
        rfc=data["rfc"],
        razon_social=data["razon_social"],
        regimen=data.get("regimen"),
        cp=data.get("cp"),
        curp=data.get("curp"),
        id_cif=data.get("id_cif"),
        source_filename=data.get("source_filename"),
        extracted_text=data.get("extracted_text"),
        qr_text=data.get("qr_text"),
        qr_valid=data.get("qr_valid"),
        qr_online=data.get("qr_online"),
        parser_source=data.get("parser_source", "regex"),
        processing_status=status,
        status_reason=reason,
        csf_hash=data["csf_hash"],
        version=1,
    )
    try:
        db.add(csf)
        db.commit()
        db.refresh(csf)
        return csf, True
    except IntegrityError:
        db.rollback()
        existing = db.query(CSF).filter_by(csf_hash=data["csf_hash"]).first()
        return existing, False


@router.post("/pdf", response_model=UploadResult)
async def upload_pdf(
    file: UploadFile = File(...),
    company_id: str = Form(default=None),
    qr_text: str = Form(default=None),
    validate_online: bool = Form(default=False),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    t0 = time.monotonic()
    cid = enforce_tenant_scope(ctx, company_id)
    content = await file.read()

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Se requiere un archivo PDF.")

    ingest.save_upload(content, file.filename)
    try:
        data = ingest.process_pdf(content, cid, validate_online=validate_online)
    except ValueError as exc:
        telemetry.record_event(success=False, latency_ms=(time.monotonic() - t0) * 1000)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data["source_filename"] = file.filename

    if qr_text:
        data["qr_text"] = qr_text

    csf, created = _persist_csf(data, db)
    elapsed = (time.monotonic() - t0) * 1000
    telemetry.record_event(success=True, latency_ms=elapsed)

    return UploadResult(
        csf_id=csf.id,
        rfc=data.get("rfc") or csf.rfc,
        razon_social=data.get("razon_social") or csf.razon_social,
        regimen=data.get("regimen"),
        cp=data.get("cp"),
        curp=data.get("curp"),
        id_cif=data.get("id_cif"),
        source_filename=csf.source_filename,
        uploaded_at=csf.uploaded_at,
        csf_hash=data.get("csf_hash") or csf.csf_hash,
        qr_text=data.get("qr_text"),
        extracted_text_preview=(data.get("extracted_text") or "")[:600],
        qr_valid=data.get("qr_valid"),
        qr_online=data.get("qr_online"),
        parser_source=csf.parser_source,
        crm_autofill=data.get("crm_autofill"),
        geolocation=data.get("geolocation"),
        ai_field_corrections=data.get("ai_field_corrections"),
        corrected_json=data.get("corrected_json"),
        processing_status=csf.processing_status,
        status_reason=csf.status_reason,
        skipped=not created,
        reason="duplicate" if not created else None,
    )


@router.post("/zip", response_model=List[UploadResult])
async def upload_zip(
    file: UploadFile = File(...),
    company_id: str = Form(default=None),
    validate_online: bool = Form(default=False),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    t0 = time.monotonic()
    cid = enforce_tenant_scope(ctx, company_id)
    content = await file.read()

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Se requiere un archivo ZIP.")

    ingest.save_upload(content, file.filename)
    raw_results = ingest.process_zip(content, cid, validate_online=validate_online)

    output: List[UploadResult] = []
    for item in raw_results:
        if "error" in item:
            output.append(UploadResult(skipped=True, reason=item["error"]))
            telemetry.record_event(success=False, latency_ms=0)
            continue

        item["source_filename"] = item.get("filename")

        csf, created = _persist_csf(item, db)
        elapsed = (time.monotonic() - t0) * 1000
        telemetry.record_event(success=True, latency_ms=elapsed)

        output.append(UploadResult(
            csf_id=csf.id,
            rfc=item.get("rfc") or csf.rfc,
            razon_social=item.get("razon_social") or csf.razon_social,
            regimen=item.get("regimen"),
            cp=item.get("cp"),
            curp=item.get("curp"),
            id_cif=item.get("id_cif"),
            source_filename=csf.source_filename,
            uploaded_at=csf.uploaded_at,
            csf_hash=item.get("csf_hash") or csf.csf_hash,
            qr_text=item.get("qr_text"),
            extracted_text_preview=(item.get("extracted_text") or "")[:600],
            qr_valid=item.get("qr_valid"),
            qr_online=item.get("qr_online"),
            parser_source=csf.parser_source,
            crm_autofill=item.get("crm_autofill"),
            geolocation=item.get("geolocation"),
            ai_field_corrections=item.get("ai_field_corrections"),
            corrected_json=item.get("corrected_json"),
            processing_status=csf.processing_status,
            status_reason=csf.status_reason,
            skipped=not created,
            reason="duplicate" if not created else None,
        ))

    return output
