from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard
from app.db.session import get_db
from app.services.backup_service import export_local_backup

router = APIRouter(prefix="/admin/backups", tags=["backups"])


@router.post("/export")
def export_backup(
    company_id: Optional[str] = Query(default=None),
    ctx: SecurityContext = Depends(role_guard("admin", "superadmin")),
    db: Session = Depends(get_db),
):
    tenant_id = enforce_tenant_scope(ctx, company_id)

    try:
        result = export_local_backup(db, tenant_id=tenant_id, requested_by=ctx.user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo generar el respaldo: {exc}") from exc

    return result