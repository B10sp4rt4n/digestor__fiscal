"""
Router de consulta de constancias — listado e individual.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, can_access_tenant, enforce_tenant_scope, role_guard
from app.db.session import get_db
from app.models.csf import CSF
from app.schemas.csf import CSFDashboardMetrics, CSFListResponse, CSFOut, CSFRegimenMetric, CSFStatusMetric, RevalidateQRResult
from app.services import ingest

router = APIRouter(prefix="/csf", tags=["csf"])


@router.get("", response_model=CSFListResponse)
def list_csf(
    company_id: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Busca por RFC o razón social"),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    tenant_id = enforce_tenant_scope(ctx, company_id)
    query = db.query(CSF)
    query = query.filter(CSF.company_id == tenant_id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(CSF.rfc.ilike(like), CSF.razon_social.ilike(like)))

    rows = query.order_by(CSF.uploaded_at.desc(), CSF.issued_at.desc()).limit(limit).all()
    return CSFListResponse(items=rows, total=len(rows))


@router.get("/dashboard", response_model=CSFDashboardMetrics)
def csf_dashboard(
    company_id: Optional[str] = Query(default=None),
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    tenant_id = enforce_tenant_scope(ctx, company_id)
    base_query = db.query(CSF).filter(CSF.company_id == tenant_id)

    total_csf = base_query.count()
    qr_valid_count = base_query.filter(CSF.qr_valid.is_(True)).count()
    qr_invalid_count = base_query.filter(CSF.qr_valid.is_(False)).count()
    qr_pending_count = base_query.filter(CSF.qr_valid.is_(None)).count()
    with_source_file_count = base_query.filter(CSF.source_filename.is_not(None)).count()

    recent_uploads = (
        base_query.order_by(CSF.uploaded_at.desc(), CSF.issued_at.desc()).limit(10).all()
    )

    regimen_rows = (
        db.query(
            func.coalesce(CSF.regimen, "Sin régimen identificado").label("regimen"),
            func.count(CSF.id).label("total"),
        )
        .filter(CSF.company_id == tenant_id)
        .group_by(func.coalesce(CSF.regimen, "Sin régimen identificado"))
        .order_by(func.count(CSF.id).desc(), func.coalesce(CSF.regimen, "Sin régimen identificado"))
        .limit(8)
        .all()
    )

    regimen_breakdown = [
        CSFRegimenMetric(regimen=row.regimen, total=row.total)
        for row in regimen_rows
    ]

    status_rows = (
        db.query(
            func.coalesce(CSF.processing_status, "processed").label("status"),
            func.count(CSF.id).label("total"),
        )
        .filter(CSF.company_id == tenant_id)
        .group_by(func.coalesce(CSF.processing_status, "processed"))
        .order_by(func.count(CSF.id).desc(), func.coalesce(CSF.processing_status, "processed"))
        .all()
    )

    status_breakdown = [
        CSFStatusMetric(status=row.status, total=row.total)
        for row in status_rows
    ]

    return CSFDashboardMetrics(
        total_csf=total_csf,
        qr_valid_count=qr_valid_count,
        qr_invalid_count=qr_invalid_count,
        qr_pending_count=qr_pending_count,
        with_source_file_count=with_source_file_count,
        status_breakdown=status_breakdown,
        recent_uploads=recent_uploads,
        regimen_breakdown=regimen_breakdown,
    )


@router.get("/{csf_id}", response_model=CSFOut)
def get_csf(
    csf_id: str,
    include_geo: bool = Query(default=False),
    include_ai_corrections: bool = Query(default=False),
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    row = db.query(CSF).filter(CSF.id == csf_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="CSF no encontrada.")
    if not can_access_tenant(ctx, row.company_id):
        raise HTTPException(status_code=403, detail="No puedes acceder a otro tenant.")

    data = CSFOut.model_validate(row).model_dump()
    data["crm_autofill"] = None
    data["geolocation"] = None
    data["ai_field_corrections"] = []
    data["corrected_json"] = None

    if include_geo or include_ai_corrections:
        parsed_pairs = ingest._extract_colon_pairs(row.extracted_text or "")
        field_map = {
            "rfc": row.rfc,
            "razon_social": row.razon_social,
            "regimen": row.regimen,
            "cp": row.cp,
            "curp": row.curp,
            "id_cif": row.id_cif,
            "qr_text": row.qr_text,
        }
        crm = ingest._build_crm_autofill(field_map, parsed_pairs)
        geo = None

        if include_geo:
            geo = ingest._resolve_geolocation(row.cp, crm)
            if geo:
                if geo.get("latitude") is not None:
                    crm.setdefault("geo_latitude", str(geo["latitude"]))
                if geo.get("longitude") is not None:
                    crm.setdefault("geo_longitude", str(geo["longitude"]))
                if geo.get("city"):
                    crm.setdefault("geo_city", geo["city"])
                if geo.get("state"):
                    crm.setdefault("geo_state", geo["state"])

        data["crm_autofill"] = crm or None
        data["geolocation"] = geo
        if include_ai_corrections and crm:
            data["ai_field_corrections"] = ingest._suggest_field_corrections_via_groq(
                crm,
                row.extracted_text or "",
            )
            data["corrected_json"] = ingest._build_corrected_json(
                crm,
                data["ai_field_corrections"],
            )
    return data


@router.post("/{csf_id}/revalidate", response_model=RevalidateQRResult)
def revalidate_csf_qr(
    csf_id: str,
    validate_online: bool = Query(default=True),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    row = db.query(CSF).filter(CSF.id == csf_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="CSF no encontrada.")
    if not can_access_tenant(ctx, row.company_id):
        raise HTTPException(status_code=403, detail="No puedes acceder a otro tenant.")
    if not row.qr_text:
        raise HTTPException(status_code=400, detail="La CSF no tiene QR persistido.")

    qr_status = ingest.validate_qr_status(row.qr_text, validate_online=validate_online)
    row.qr_valid = qr_status.get("qr_valid")
    row.qr_online = qr_status.get("qr_online")
    if row.qr_valid is True:
        row.processing_status = "processed"
        row.status_reason = None
    elif row.qr_valid is False:
        row.processing_status = "needs_review"
        row.status_reason = "qr_invalid"
    else:
        row.processing_status = "pending_qr"
        row.status_reason = "qr_not_detected"
    db.commit()
    db.refresh(row)

    return RevalidateQRResult(
        csf_id=row.id,
        qr_text=row.qr_text,
        qr_valid=row.qr_valid,
        qr_online=row.qr_online,
    )