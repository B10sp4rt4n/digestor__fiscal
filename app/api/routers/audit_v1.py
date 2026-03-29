from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard
from app.db.session import get_db
from app.schemas.audit import AuditResponse, AuditChainIntegrity
from app.services import audit_service

router = APIRouter(prefix="/v1/audit", tags=["audit-v1"])


@router.get("", response_model=AuditResponse)
def get_audit_trail(
    company_id: str = Query(default=None),
    entity_id: str = Query(default=None, description="Filtrar por entity_id (ej: CSF id o job_id)"),
    entity_type: str = Query(default=None, description="Filtrar por tipo (ej: document_job, csf)"),
    limit: int = Query(default=100, ge=1, le=1000),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    """Obtener trail de auditoría inmutable con verificación de integridad."""
    cid = enforce_tenant_scope(ctx, company_id)
    
    records = audit_service.get_audit_records(db, cid, entity_id=entity_id, entity_type=entity_type, limit=limit)
    is_valid, error_msg = audit_service.verify_audit_chain(db, cid)
    
    return AuditResponse(
        company_id=cid,
        records=records,
        chain_integrity=AuditChainIntegrity(
            is_valid=is_valid,
            message=error_msg,
            total_records=len(records),
        ),
    )


@router.get("/verify", response_model=AuditChainIntegrity)
def verify_chain(
    company_id: str = Query(default=None),
    ctx: SecurityContext = Depends(role_guard("admin", "superadmin")),
    db: Session = Depends(get_db),
):
    """Verificar integridad de la cadena de auditoría (solo admin/superadmin)."""
    cid = enforce_tenant_scope(ctx, company_id)
    is_valid, error_msg = audit_service.verify_audit_chain(db, cid)
    total = db.query(audit_service.AuditLog).filter_by(company_id=cid).count()
    
    return AuditChainIntegrity(
        is_valid=is_valid,
        message=error_msg,
        total_records=total,
    )
