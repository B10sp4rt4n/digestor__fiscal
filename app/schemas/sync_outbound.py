from datetime import datetime, timezone
import secrets
from typing import Any

from pydantic import BaseModel, Field


class DocumentApproveRequest(BaseModel):
    company_id: str | None = Field(default=None, description="Opcional. Déjalo vacío en Swagger; el tenant se detecta desde el documento.")
    notes: str | None = Field(default=None, description="Notas opcionales de aprobación.")

    model_config = {
        "json_schema_extra": {
            "example": {}
        }
    }


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
    document_id: str = Field(description="UUID del documento ya aprobado para sync.")
    document_type: str = Field(default="csf", description="Tipo de documento. Para este flujo usa 'csf'.")
    csf_hash: str | None = Field(default=None, description="Opcional. Se auto-completa desde BD si lo omites.")
    normalized_payload: dict[str, Any] | None = Field(default=None, description="Opcional. Se auto-completa desde BD si lo omites.")
    quality: OutboundQuality | None = Field(default=None, description="Opcional. Se auto-completa desde BD si lo omites.")


class OutboundSyncRequest(BaseModel):
    contract_version: str = Field(default="v1.0", description="Versión del contrato. Déjalo como v1.0.")
    event_type: str = Field(default="csf_sync", description="Tipo de evento. Por defecto csf_sync.")
    event_id: str = Field(default_factory=lambda: f"evt-{secrets.token_hex(6)}", description="Opcional. Se genera automáticamente si no lo mandas.")
    event_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Opcional. Se genera automáticamente en UTC.")
    company_id: str | None = Field(default=None, description="Opcional. Déjalo vacío para usar el tenant del token.")
    document: OutboundDocument

    model_config = {
        "json_schema_extra": {
            "example": {
                "document": {
                    "document_id": "UUID_DEL_DOCUMENTO_APROBADO"
                }
            }
        }
    }


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
