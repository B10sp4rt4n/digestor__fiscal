"""
Servicio de gestión de jobs asíncrónos para procesamiento de documentos.
"""
import time
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.document_job import DocumentJob


def create_job(db: Session, company_id: str, document_type: str) -> str:
    """Crear un nuevo job en estado queued."""
    job = DocumentJob(
        company_id=company_id,
        document_type=document_type,
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job.id


def get_job(db: Session, job_id: str) -> DocumentJob | None:
    """Obtener un job por ID."""
    return db.query(DocumentJob).filter_by(id=job_id).first()


def update_job_processing(db: Session, job_id: str) -> None:
    """Cambiar status a processing."""
    job = db.query(DocumentJob).filter_by(id=job_id).first()
    if job:
        job.status = "processing"
        job.started_at = datetime.utcnow()
        db.commit()


def update_job_done(db: Session, job_id: str, document_id: str, processing_time_ms: int) -> None:
    """Cambiar status a done con el document_id y tiempo de procesamiento."""
    job = db.query(DocumentJob).filter_by(id=job_id).first()
    if job:
        job.status = "done"
        job.document_id = document_id
        job.completed_at = datetime.utcnow()
        job.processing_time_ms = processing_time_ms
        db.commit()


def update_job_failed(db: Session, job_id: str, error_msg: str) -> None:
    """Cambiar status a failed con mensaje de error."""
    job = db.query(DocumentJob).filter_by(id=job_id).first()
    if job:
        job.status = "failed"
        job.error = error_msg
        job.completed_at = datetime.utcnow()
        db.commit()
