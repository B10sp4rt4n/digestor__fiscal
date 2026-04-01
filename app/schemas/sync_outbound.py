from datetime import datetime, timezone
import secrets
from typing import Any

from pydantic import BaseModel, Field


class DocumentApproveRequest(BaseModel):
    company_id: str | None = None
    notes: str | None = None


class DocumentApproveResponse(BaseModel):
    document_id: str
    company_id: str
    status: str
    approved_at: datetime


class OutboundQuality(BaseModel):
    required_fields_ok: bool = False
    score: float = 0.0
    missing_fields: list[str] = Field(default_factory=list)


class OutboundDocument(BaseModel):
    document_id: str
    document_type: str = "csf"
    csf_hash: str | None = None
    normalized_payload: dict[str, Any] | None = None
    quality: OutboundQuality | None = None


class OutboundSyncRequest(BaseModel):
    contract_version: str = "v1.0"
    event_type: str = "csf_sync"
    event_id: str = Field(default_factory=lambda: f"evt-{secrets.token_hex(6)}")
    event_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    company_id: str | None = None
    document: OutboundDocument


class OutboundSyncResponse(BaseModel):
    accepted: bool
    event_id: str
    delivery_status: str


class OutboundSyncStatusResponse(BaseModel):
    event_id: str
    company_id: str
    document_id: str
    delivery_status: str
    payload_preview: dict[str, Any] | None = None
    attempts: int
    last_error: str | None = None
    last_attempt_at: datetime | None = None
    next_retry_at: datetime | None = None
    delivered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
