from typing import Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.document_job import DocumentJob
from app.models.document_sync_event import DocumentSyncEvent
from app.models.csf import CSF


def get_tenant_metrics(db: Session, company_id: str, hours: int = 24) -> dict[str, Any]:
    """Obtener métricas agregadas del tenant en las últimas N horas."""
    cutoff_time = datetime.utcnow() - timedelta(hours=hours)

    # Jobs totales y por estado
    total_jobs = db.query(DocumentJob).filter_by(company_id=company_id).count()
    queued_jobs = db.query(DocumentJob).filter(
        DocumentJob.company_id == company_id,
        DocumentJob.status == "queued",
    ).count()
    processing_jobs = db.query(DocumentJob).filter(
        DocumentJob.company_id == company_id,
        DocumentJob.status == "processing",
    ).count()
    done_jobs = db.query(DocumentJob).filter(
        DocumentJob.company_id == company_id,
        DocumentJob.status == "done",
        DocumentJob.created_at >= cutoff_time,
    ).count()
    failed_jobs = db.query(DocumentJob).filter(
        DocumentJob.company_id == company_id,
        DocumentJob.status == "failed",
        DocumentJob.created_at >= cutoff_time,
    ).count()

    # Documentos totales
    total_csf = db.query(CSF).filter_by(company_id=company_id).count()
    csf_recent = db.query(CSF).filter(
        CSF.company_id == company_id,
        CSF.uploaded_at >= cutoff_time,
    ).count()

    # Tiempo promedio de procesamiento (últimas 24h)
    avg_processing_time = db.query(func.avg(DocumentJob.processing_time_ms)).filter(
        DocumentJob.company_id == company_id,
        DocumentJob.status == "done",
        DocumentJob.completed_at >= cutoff_time,
    ).scalar() or 0

    # Porcentaje de éxito
    success_rate = 0.0
    total_completed = done_jobs + failed_jobs
    if total_completed > 0:
        success_rate = (done_jobs / total_completed) * 100

    return {
        "tenant_id": company_id,
        "timestamp": datetime.utcnow().isoformat(),
        "period_hours": hours,
        "jobs": {
            "total": total_jobs,
            "queued": queued_jobs,
            "processing": processing_jobs,
            "done": done_jobs,
            "failed": failed_jobs,
            "success_rate_percent": round(success_rate, 2),
        },
        "documents": {
            "total_csf": total_csf,
            "csf_recent": csf_recent,
        },
        "performance": {
            "avg_processing_time_ms": round(avg_processing_time, 2),
        },
    }


def get_tenant_alerts(db: Session, company_id: str) -> list[dict[str, Any]]:
    """Generar alertas operativas para el tenant."""
    alerts = []

    # Alerta 1: Jobs en cola sin procesar por mucho tiempo
    stale_queued = db.query(DocumentJob).filter(
        DocumentJob.company_id == company_id,
        DocumentJob.status == "queued",
        DocumentJob.created_at < datetime.utcnow() - timedelta(minutes=10),
    ).count()
    if stale_queued > 0:
        alerts.append({
            "severity": "warning",
            "type": "stale_queue",
            "message": f"{stale_queued} documentos en cola sin procesarse por más de 10 minutos.",
        })

    # Alerta 2: Tasa de fallos muy alta
    recent_completed = db.query(DocumentJob).filter(
        DocumentJob.company_id == company_id,
        DocumentJob.status.in_(["done", "failed"]),
        DocumentJob.completed_at >= datetime.utcnow() - timedelta(hours=1),
    ).count()
    recent_failed = db.query(DocumentJob).filter(
        DocumentJob.company_id == company_id,
        DocumentJob.status == "failed",
        DocumentJob.completed_at >= datetime.utcnow() - timedelta(hours=1),
    ).count()
    if recent_completed > 10 and recent_failed / recent_completed > 0.25:
        alerts.append({
            "severity": "critical",
            "type": "high_failure_rate",
            "message": f"Tasa de fallos {(recent_failed/recent_completed*100):.1f}% en la última hora.",
        })

    # Alerta 3: Jobs procesándose por mucho tiempo
    slow_processing = db.query(DocumentJob).filter(
        DocumentJob.company_id == company_id,
        DocumentJob.status == "processing",
        DocumentJob.started_at < datetime.utcnow() - timedelta(minutes=5),
    ).count()
    if slow_processing > 0:
        alerts.append({
            "severity": "info",
            "type": "slow_processing",
            "message": f"{slow_processing} documentos procesándose por más de 5 minutos.",
        })

    return alerts


def get_outbound_metrics(db: Session, company_id: str, hours: int = 24) -> dict[str, Any]:
    cutoff_time = datetime.utcnow() - timedelta(hours=hours)

    queued = db.query(DocumentSyncEvent).filter(
        DocumentSyncEvent.company_id == company_id,
        DocumentSyncEvent.delivery_status == "queued",
    ).count()
    retry = db.query(DocumentSyncEvent).filter(
        DocumentSyncEvent.company_id == company_id,
        DocumentSyncEvent.delivery_status == "retry",
    ).count()
    delivered = db.query(DocumentSyncEvent).filter(
        DocumentSyncEvent.company_id == company_id,
        DocumentSyncEvent.delivery_status == "delivered",
        DocumentSyncEvent.updated_at >= cutoff_time,
    ).count()
    failed_dlq = db.query(DocumentSyncEvent).filter(
        DocumentSyncEvent.company_id == company_id,
        DocumentSyncEvent.delivery_status == "failed_dlq",
        DocumentSyncEvent.updated_at >= cutoff_time,
    ).count()

    total = queued + retry + delivered + failed_dlq

    oldest_pending = db.query(DocumentSyncEvent).filter(
        DocumentSyncEvent.company_id == company_id,
        DocumentSyncEvent.delivery_status.in_(["queued", "retry"]),
    ).order_by(DocumentSyncEvent.created_at.asc()).first()

    oldest_pending_age_seconds = 0.0
    if oldest_pending and oldest_pending.created_at:
        oldest_pending_age_seconds = (datetime.utcnow() - oldest_pending.created_at).total_seconds()

    completed = delivered + failed_dlq
    success_rate = (delivered / completed * 100.0) if completed > 0 else 0.0

    return {
        "company_id": company_id,
        "period_hours": hours,
        "queued": queued,
        "retry": retry,
        "delivered": delivered,
        "failed_dlq": failed_dlq,
        "total": total,
        "oldest_pending_age_seconds": round(oldest_pending_age_seconds, 2),
        "success_rate_percent": round(success_rate, 2),
    }
