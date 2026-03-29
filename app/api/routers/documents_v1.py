import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from sqlalchemy.orm import Session

from app.api.routers.upload import _persist_csf
from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard, can_access_tenant
from app.db.session import get_db
from app.models.document_job import DocumentJob
from app.schemas.document_api import UniversalDocumentResponse, UniversalDocumentResult
from app.services import ingest, job_service, audit_service

router = APIRouter(prefix="/v1/documents", tags=["documents-v1"])


def _process_document_csf(
    db: Session,
    job_id: str,
    company_id: str,
    content: bytes,
    filename: str,
    validate_online: bool = False,
) -> tuple[Optional[str], Optional[str]]:
    """Procesa un documento CSF y retorna (document_id, error)."""
    t0 = time.monotonic()
    try:
        job_service.update_job_processing(db, job_id)

        ingest.save_upload(content, filename)
        data = ingest.process_pdf(content, company_id, validate_online=validate_online)
        data["source_filename"] = filename

        csf, _created = _persist_csf(data, db, pdf_bytes=content)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        job_service.update_job_done(db, job_id, csf.id, elapsed_ms)
        return csf.id, None
    except Exception as exc:
        error_msg = str(exc)
        job_service.update_job_failed(db, job_id, error_msg)
        return None, error_msg


@router.post("", response_model=UniversalDocumentResponse)
async def ingest_document(
    file: UploadFile = File(...),
    document_type: str = Form(default="csf"),
    company_id: str = Form(default=None),
    validate_online: bool = Form(default=False),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
    request: Request = None,
):
    """Contrato universal v1 para ingesta de documentos.

    Devuelve job_id inmediatamente. Usa GET /v1/{job_id} para consultar progreso.
    """
    cid = enforce_tenant_scope(ctx, company_id)

    if document_type != "csf":
        raise HTTPException(status_code=400, detail="document_type no soportado. Usa 'csf'.")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Para document_type=csf se requiere un PDF.")

    # Crear job en BD
    job_id = job_service.create_job(db, cid, document_type)
    
    # Registrar en auditoría: creación de job
    audit_service.create_audit_log(
        db,
        company_id=cid,
        user_id=ctx.user_id,
        action="document_upload_start",
        entity_type="document_job",
        entity_id=job_id,
        details={
            "filename": file.filename,
            "document_type": document_type,
            "validate_online": validate_online,
        },
        ip_address=request.client.host if request else None,
        user_agent=request.headers.get("user-agent") if request else None,
        status="success",
    )

    # Leer contenido del archivo
    content = await file.read()

    # NOTA: Por ahora procesamos sincronamente aquí.
    # En producción, encolar en Redis/Celery y devolver inmediatamente.
    document_id, error = _process_document_csf(db, job_id, cid, content, file.filename, validate_online)

    job = job_service.get_job(db, job_id)
    
    # Registrar en auditoría: resultado del procesamiento
    audit_service.create_audit_log(
        db,
        company_id=cid,
        user_id=ctx.user_id,
        action="document_upload_complete",
        entity_type="document_job",
        entity_id=job_id,
        details={
            "status": job.status,
            "document_id": document_id,
            "error": error,
        },
        ip_address=request.client.host if request else None,
        user_agent=request.headers.get("user-agent") if request else None,
        status="success" if not error else "error",
        error_message=error,
    )

    return UniversalDocumentResponse(
        job_id=job_id,
        status=job.status,
        document_type=document_type,
        company_id=cid,
        document_id=document_id,
        error=error,
    )


@router.get("/{job_id}", response_model=UniversalDocumentResponse)
def get_job_status(
    job_id: str,
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    """Consultar estado de un job de procesamiento de documento."""
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado.")

    # Verificar que el usuario tenga acceso al tenant del job
    if not can_access_tenant(ctx, job.company_id):
        raise HTTPException(status_code=403, detail="No tienes acceso a este job.")

    result = None
    if job.status == "done" and job.document_id:
        result = UniversalDocumentResult(
            raw_text="",
            normalized_fields={"document_id": job.document_id},
            validation_flags={},
            artifacts={
                "pdf_download_path": f"/csf/{job.document_id}/pdf" if job.document_type == "csf" else None,
                "processing_time_ms": job.processing_time_ms,
            },
        )

    return UniversalDocumentResponse(
        job_id=job_id,
        status=job.status,
        document_type=job.document_type,
        company_id=job.company_id,
        document_id=job.document_id,
        error=job.error,
        result=result,
    )
