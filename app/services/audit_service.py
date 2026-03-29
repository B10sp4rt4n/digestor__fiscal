"""
Servicio de Audit Trail Inmutable con Cadena de Hashes Criptográficos
"""
import hashlib
import json
from datetime import datetime
from typing import Optional, Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.audit_log import AuditLog


def _compute_content_hash(action: str, entity_type: str, entity_id: str, details: Optional[str]) -> str:
    """Calcular SHA256 del contenido de una entrada de auditoría."""
    content = f"{action}|{entity_type}|{entity_id}|{details or ''}"
    return hashlib.sha256(content.encode()).hexdigest()


def _compute_chain_hash(previous_hash: Optional[str], content_hash: str) -> str:
    """Calcular SHA256 de la cadena: hash_anterior + hash_actual."""
    chain_input = f"{previous_hash or ''}|{content_hash}"
    return hashlib.sha256(chain_input.encode()).hexdigest()


def get_last_audit_log(db: Session, company_id: str) -> Optional[AuditLog]:
    """Obtener el último audit log de una empresa para continuar la cadena."""
    return db.query(AuditLog).filter_by(company_id=company_id).order_by(AuditLog.sequence_number.desc()).first()


def create_audit_log(
    db: Session,
    company_id: str,
    user_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    status: str = "success",
    error_message: Optional[str] = None,
) -> AuditLog:
    """Crear una nueva entrada de auditoría con cadena inmutable."""
    
    # Obtener la última entrada para enlazar la cadena
    last_log = get_last_audit_log(db, company_id)
    previous_hash = last_log.chain_hash if last_log else None
    sequence_number = (last_log.sequence_number + 1) if last_log else 1
    
    # Serializar detalles
    details_json = json.dumps(details or {})
    
    # Calcular hashes
    content_hash = _compute_content_hash(action, entity_type, entity_id, details_json)
    chain_hash = _compute_chain_hash(previous_hash, content_hash)
    
    # Crear entrada
    log = AuditLog(
        company_id=company_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details_json,
        previous_hash=previous_hash,
        content_hash=content_hash,
        chain_hash=chain_hash,
        ip_address=ip_address,
        user_agent=user_agent,
        status=status,
        error_message=error_message,
        sequence_number=sequence_number,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def verify_audit_chain(db: Session, company_id: str) -> tuple[bool, Optional[str]]:
    """Verificar integridad de la cadena de auditoría de una empresa.
    
    Retorna: (es_válida, mensaje_de_error_o_none)
    """
    logs = db.query(AuditLog).filter_by(company_id=company_id).order_by(AuditLog.sequence_number).all()
    
    if not logs:
        return True, None
    
    previous_hash = None
    for i, log in enumerate(logs):
        # Verificar que el previous_hash es correcto
        if log.previous_hash != previous_hash:
            return False, f"Ruptura en cadena en entrada {i+1}: previous_hash no coincide"
        
        # Recalcular content_hash
        recalc_content = _compute_content_hash(log.action, log.entity_type, log.entity_id, log.details)
        if recalc_content != log.content_hash:
            return False, f"Corrupción en entrada {i+1}: content_hash no coincide"
        
        # Recalcular chain_hash
        recalc_chain = _compute_chain_hash(previous_hash, log.content_hash)
        if recalc_chain != log.chain_hash:
            return False, f"Corrupción en entrada {i+1}: chain_hash no coincide"
        
        previous_hash = log.chain_hash
    
    return True, None


def get_audit_records(
    db: Session,
    company_id: str,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Obtener registros de auditoría con opción de filtros."""
    query = db.query(AuditLog).filter_by(company_id=company_id)
    
    if entity_id:
        query = query.filter_by(entity_id=entity_id)
    if entity_type:
        query = query.filter_by(entity_type=entity_type)
    
    logs = query.order_by(AuditLog.sequence_number.desc()).limit(limit).all()
    
    return [
        {
            "id": log.id,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "user_id": log.user_id,
            "ip_address": log.ip_address,
            "status": log.status,
            "created_at": log.created_at.isoformat(),
            "sequence": log.sequence_number,
            "chain_hash": log.chain_hash,
            "details": json.loads(log.details) if log.details else {},
        }
        for log in logs
    ]
