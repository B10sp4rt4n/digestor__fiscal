from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel


class CSFBase(BaseModel):
    company_id: str
    rfc: str
    razon_social: str
    regimen: Optional[str] = None
    cp: Optional[str] = None
    curp: Optional[str] = None
    id_cif: Optional[str] = None
    source_filename: Optional[str] = None


class CSFSummary(CSFBase):
    id: str
    csf_hash: str
    version: int
    parser_source: str = "regex"
    processing_status: str = "processed"
    status_reason: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    issued_at: Optional[datetime] = None
    qr_valid: Optional[bool] = None
    qr_online: Optional[bool] = None

    model_config = {"from_attributes": True}


class CSFCreate(CSFBase):
    csf_hash: str
    version: int = 1


class CSFOut(CSFSummary):
    qr_text: Optional[str] = None
    extracted_text: Optional[str] = None
    crm_autofill: Optional[dict[str, str]] = None
    geolocation: Optional[dict[str, Any]] = None
    ai_field_corrections: Optional[list[dict[str, Any]]] = None

    model_config = {"from_attributes": True}


class CSFListResponse(BaseModel):
    items: list[CSFSummary]
    total: int


class CSFRegimenMetric(BaseModel):
    regimen: str
    total: int


class CSFStatusMetric(BaseModel):
    status: str
    total: int


class CSFDashboardMetrics(BaseModel):
    total_csf: int
    qr_valid_count: int
    qr_invalid_count: int
    qr_pending_count: int
    with_source_file_count: int
    status_breakdown: list[CSFStatusMetric]
    recent_uploads: list[CSFSummary]
    regimen_breakdown: list[CSFRegimenMetric]


class UploadResult(BaseModel):
    csf_id: Optional[str] = None
    rfc: Optional[str] = None
    razon_social: Optional[str] = None
    regimen: Optional[str] = None
    cp: Optional[str] = None
    curp: Optional[str] = None
    id_cif: Optional[str] = None
    source_filename: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    csf_hash: Optional[str] = None
    qr_text: Optional[str] = None
    extracted_text_preview: Optional[str] = None
    qr_valid: Optional[bool] = None
    qr_online: Optional[bool] = None
    parser_source: Optional[str] = None
    crm_autofill: Optional[dict[str, str]] = None
    geolocation: Optional[dict[str, Any]] = None
    ai_field_corrections: Optional[list[dict[str, Any]]] = None
    processing_status: Optional[str] = None
    status_reason: Optional[str] = None
    skipped: bool = False
    reason: Optional[str] = None


class RevalidateQRResult(BaseModel):
    csf_id: str
    qr_text: Optional[str] = None
    qr_valid: Optional[bool] = None
    qr_online: Optional[bool] = None
