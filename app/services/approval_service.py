"""Server-owned approval snapshots. No AI or network calls at approval time."""
import uuid
from datetime import datetime

from fastapi import HTTPException

from app.models.document_job import DocumentJob
from app.services import audit_service, ingest

FIELD_MAP = {
    'tax_id': 'rfc', 'legal_name': 'razon_social', 'tax_regime': 'regimen',
    'postal_code': 'cp', 'cif_id': 'id_cif',
}


def document_values(row):
    return {
        'document_id': row.id,
        'document_type': 'csf',
        'csf_hash': row.csf_hash,
        'normalized_payload': {name: getattr(row, name) for name in (*FIELD_MAP.values(), 'curp')},
        'quality': {'required_fields_ok': True, 'score': 1.0, 'missing_fields': []},
    }


def validation_error(reason, **details):
    raise HTTPException(422, detail={
        'error_code': 'VALIDATION_FAILED', 'message': reason,
        'details': details, 'retryable': False,
    })


def approve(db, row, actor, notes):
    # Strict V1: legacy uploads without a successful job must be reingested via V1.
    job = (db.query(DocumentJob)
           .filter_by(company_id=row.company_id, document_id=row.id)
           .order_by(DocumentJob.created_at.desc(), DocumentJob.id.desc()).first())
    if not job or job.status != 'done' or job.error:
        validation_error('Se requiere un job V1 terminado sin error.', required_action='reingest_v1_if_legacy')
    if row.processing_status not in {'processed', 'pending_qr', 'duplicate', 'approved_for_sync'}:
        validation_error('El documento requiere revisión antes de aprobarse.', status=row.processing_status)

    crm = {key: getattr(row, attr) for key, attr in FIELD_MAP.items()}
    fresh = {item['field']: item for item in ingest._build_field_validation_report(crm)}
    stored = {item.get('field'): item for item in (row.field_validation or []) if isinstance(item, dict)}
    invalid = []
    for key in FIELD_MAP:
        item = stored.get(key, {})
        current = str(crm[key] or '').strip()
        if (not fresh[key]['is_ok'] or item.get('status') != 'si'
                or item.get('is_ok') is not True
                or str(item.get('value') or '').strip() != current
                or current.upper() in {'DESCONOCIDO', 'SIN NOMBRE'}):
            invalid.append(key)
    if invalid:
        validation_error('Campos requeridos inválidos, no revisados o desactualizados.', invalid_fields=invalid)

    snapshot = document_values(row)
    prior = dict(row.approval_snapshot or {})
    prior.pop('approval_id', None)
    if (row.processing_status == 'approved_for_sync' and row.approval_id
            and row.approved_at and row.approved_by and prior == snapshot):
        return row  # Retry: preserve original actor, instant and snapshot.

    row.approval_id = str(uuid.uuid4())
    snapshot['approval_id'] = row.approval_id
    row.approval_snapshot = snapshot
    row.approved_at = datetime.utcnow()
    row.approved_by = actor
    row.processing_status = 'approved_for_sync'
    row.status_reason = f'approved_by:{actor}'
    try:
        audit_service.create_audit_log(
            db, company_id=row.company_id, user_id=actor,
            action='document_approved_for_sync', entity_type='csf', entity_id=row.id,
            details={'notes': notes, 'approval_id': row.approval_id,
                     'approved_at': row.approved_at.isoformat(), 'approved_by': actor,
                     'snapshot': snapshot}, commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return row


def approved_document(row):
    if (row.processing_status != 'approved_for_sync' or not row.approval_snapshot
            or not row.approval_id or not row.approved_at or not row.approved_by):
        raise HTTPException(422, detail={
            'error_code': 'NOT_APPROVED_FOR_SYNC',
            'message': 'Se requiere aprobación con snapshot verificable.',
            'details': {'document_id': row.id}, 'retryable': False,
        })
    snapshot = dict(row.approval_snapshot)
    approval_id = snapshot.pop('approval_id', None)
    if approval_id != row.approval_id or snapshot != document_values(row):
        raise HTTPException(409, detail={
            'error_code': 'APPROVAL_STALE', 'message': 'El documento cambió; vuelve a aprobarlo.',
            'details': {'document_id': row.id}, 'retryable': False,
        })
    return dict(row.approval_snapshot)
