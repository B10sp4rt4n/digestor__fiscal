"""
Router de consulta de constancias — listado e individual.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.csf import CSF
from app.schemas.csf import CSFListResponse, CSFOut, RevalidateQRResult
from app.services import ingest

router = APIRouter(prefix="/csf", tags=["csf"])


@router.get("", response_model=CSFListResponse)
def list_csf(
    company_id: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Busca por RFC o razón social"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(CSF)
    if company_id:
        query = query.filter(CSF.company_id == company_id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(CSF.rfc.ilike(like), CSF.razon_social.ilike(like)))

    rows = query.order_by(CSF.uploaded_at.desc(), CSF.issued_at.desc()).limit(limit).all()
    return CSFListResponse(items=rows, total=len(rows))


@router.get("/{csf_id}", response_model=CSFOut)
def get_csf(csf_id: str, db: Session = Depends(get_db)):
    row = db.query(CSF).filter(CSF.id == csf_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="CSF no encontrada.")
    return row


@router.post("/{csf_id}/revalidate", response_model=RevalidateQRResult)
def revalidate_csf_qr(
    csf_id: str,
    validate_online: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    row = db.query(CSF).filter(CSF.id == csf_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="CSF no encontrada.")
    if not row.qr_text:
        raise HTTPException(status_code=400, detail="La CSF no tiene QR persistido.")

    qr_status = ingest.validate_qr_status(row.qr_text, validate_online=validate_online)
    row.qr_valid = qr_status.get("qr_valid")
    row.qr_online = qr_status.get("qr_online")
    db.commit()
    db.refresh(row)

    return RevalidateQRResult(
        csf_id=row.id,
        qr_text=row.qr_text,
        qr_valid=row.qr_valid,
        qr_online=row.qr_online,
    )