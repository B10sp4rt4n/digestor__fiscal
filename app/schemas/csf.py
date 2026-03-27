from datetime import datetime
from typing import Optional
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

    model_config = {"from_attributes": True}


class CSFListResponse(BaseModel):
    items: list[CSFSummary]
    total: int


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
    skipped: bool = False
    reason: Optional[str] = None


class RevalidateQRResult(BaseModel):
    csf_id: str
    qr_text: Optional[str] = None
    qr_valid: Optional[bool] = None
    qr_online: Optional[bool] = None
