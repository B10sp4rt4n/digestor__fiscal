from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard, can_access_tenant
from app.db.session import get_db
from app.models.csf import CSF
from app.schemas.document_api import UniversalDocumentResponse, UniversalDocumentResult
from app.schemas.sync_outbound import DocumentApproveRequest, DocumentApproveResponse
from app.services import audit_service, job_service, approval_service
from app.services.document_queue import DocumentTask, document_queue

router = APIRouter(prefix="/v1/documents", tags=["documents-v1"])


def _normalize_optional_company_id(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() == "string":
        return None
    return normalized


@router.post(
    "",
    response_model=UniversalDocumentResponse,
    summary="1) Subir documento para procesamiento",
    description="Sube un PDF de CSF. En Swagger deja `company_id` vacío; el tenant se toma del token automáticamente.",
)
async def ingest_document(
    file: UploadFile = File(..., description="Archivo PDF de la constancia o documento fiscal."),
    document_type: str = Form(default="csf", description="Tipo de documento. Para este flujo usa `csf`.", examples=["csf"]),
    company_id: str | None = Form(default=None, description="Opcional. Déjalo vacío en Swagger; el tenant se resuelve desde tu token.", examples=[""]),
    validate_online: bool = Form(default=False, description="Si true, intenta validación online adicional."),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
    request: Request = None,
):
    """Contrato universal v1 para ingesta de documentos.

    Devuelve job_id inmediatamente. Usa GET /v1/{job_id} para consultar progreso.
    """
    # Swagger suele enviar company_id="string" para campos opcionales.
    # Lo tratamos como no informado para evitar errores falsos de tenant.
    cid = enforce_tenant_scope(ctx, _normalize_optional_company_id(company_id))

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


@router.get(
    "/{job_id}",
    response_model=UniversalDocumentResponse,
    summary="2) Consultar estado del job",
    description="Pega aquí el `job_id` que te devolvió el upload. Cuando el status sea `done`, copia el `document_id`.",
)
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


@router.post(
    "/{document_id}/approve",
    response_model=DocumentApproveResponse,
    summary="3) Aprobar documento para sincronización",
    description="Pega el `document_id` y deja el body como `{}` o solo manda `notes`. No necesitas enviar `company_id`.",
)
def approve_document_for_sync(
    document_id: str,
    body: DocumentApproveRequest,
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    row = db.query(CSF).filter(CSF.id == document_id).with_for_update().first()
    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    if not can_access_tenant(ctx, row.company_id):
        raise HTTPException(status_code=403, detail="No puedes acceder a este documento.")
    approval_service.approve(db, row, ctx.user_id, body.notes)
    return DocumentApproveResponse(
        document_id=row.id, company_id=row.company_id,
        status=row.processing_status, approved_at=row.approved_at,
        approved_by=row.approved_by, approval_id=row.approval_id,
    )
