from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard, can_access_tenant
from app.db.session import get_db
from app.models.csf import CSF
from app.schemas.document_api import UniversalDocumentResponse, UniversalDocumentResult
from app.schemas.sync_outbound import DocumentApproveRequest, DocumentApproveResponse
from app.services import audit_service, job_service
from app.services.document_queue import DocumentTask, document_queue

router = APIRouter(prefix="/v1/documents", tags=["documents-v1"])


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

    await document_queue.enqueue(
        DocumentTask(
            job_id=job_id,
            company_id=cid,
            user_id=ctx.user_id,
            document_type=document_type,
            filename=file.filename,
            content=content,
            validate_online=validate_online,
            ip_address=request.client.host if request else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )

    return UniversalDocumentResponse(
        job_id=job_id,
        status="queued",
        document_type=document_type,
        company_id=cid,
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


@router.post("/{document_id}/approve", response_model=DocumentApproveResponse)
def approve_document_for_sync(
    document_id: str,
    body: DocumentApproveRequest,
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    row = db.query(CSF).filter(CSF.id == document_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")

    cid = enforce_tenant_scope(ctx, body.company_id or row.company_id)
    if row.company_id != cid:
        raise HTTPException(status_code=403, detail="No puedes aprobar documentos de otro tenant.")

    required_fields = {
        "tax_id": bool((row.rfc or "").strip()),
        "legal_name": bool((row.razon_social or "").strip()),
        "tax_regime": bool((row.regimen or "").strip()),
        "postal_code": bool((row.cp or "").strip()),
        "cif_id": bool((row.id_cif or "").strip()),
    }
    missing = [field for field, ok in required_fields.items() if not ok]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "VALIDATION_FAILED",
                "message": "Documento no cumple criterios de aprobacion para sync.",
                "details": {"missing_fields": missing},
            },
        )

    approved_at = datetime.utcnow()
    row.processing_status = "approved_for_sync"
    row.status_reason = f"approved_by:{ctx.user_id}"
    db.commit()
    db.refresh(row)

    audit_service.create_audit_log(
        db,
        company_id=cid,
        user_id=ctx.user_id,
        action="document_approved_for_sync",
        entity_type="csf",
        entity_id=row.id,
        details={
            "notes": body.notes,
            "processing_status": row.processing_status,
        },
        status="success",
    )

    return DocumentApproveResponse(
        document_id=row.id,
        company_id=cid,
        status=row.processing_status,
        approved_at=approved_at,
    )
