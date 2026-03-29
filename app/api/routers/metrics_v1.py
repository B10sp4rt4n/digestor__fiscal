from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard
from app.db.session import get_db
from app.schemas.metrics import TenantDashboard, TenantAlert, JobMetrics, DocumentMetrics, PerformanceMetrics
from app.services import metrics_service

router = APIRouter(prefix="/v1/metrics", tags=["metrics-v1"])


@router.get("/dashboard", response_model=TenantDashboard)
def get_dashboard(
    company_id: str = Query(default=None),
    hours: int = Query(default=24, ge=1, le=720),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    """Dashboard en tiempo real con métricas e instrumentación del tenant."""
    cid = enforce_tenant_scope(ctx, company_id)

    metrics = metrics_service.get_tenant_metrics(db, cid, hours=hours)
    alerts_list = metrics_service.get_tenant_alerts(db, cid)

    return TenantDashboard(
        tenant_id=cid,
        timestamp=metrics["timestamp"],
        period_hours=metrics["period_hours"],
        jobs=JobMetrics(**metrics["jobs"]),
        documents=DocumentMetrics(**metrics["documents"]),
        performance=PerformanceMetrics(**metrics["performance"]),
        alerts=[TenantAlert(**alert) for alert in alerts_list],
    )


@router.get("/alerts", response_model=list[TenantAlert])
def get_alerts(
    company_id: str = Query(default=None),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    """Obtener solo las alertas activas del tenant."""
    cid = enforce_tenant_scope(ctx, company_id)
    alerts_list = metrics_service.get_tenant_alerts(db, cid)
    return [TenantAlert(**alert) for alert in alerts_list]
