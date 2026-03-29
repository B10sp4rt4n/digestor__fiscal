from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class UniversalDocumentResult(BaseModel):
    raw_text: Optional[str] = None
    normalized_fields: dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = None
    validation_flags: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)


class UniversalDocumentResponse(BaseModel):
    job_id: str
    status: Literal["queued", "processing", "done", "failed"]
    document_type: str
    company_id: str
    document_id: Optional[str] = None
    result: Optional[UniversalDocumentResult] = None
    error: Optional[str] = None
