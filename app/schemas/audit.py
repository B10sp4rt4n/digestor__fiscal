from typing import Any, Optional
from datetime import datetime
from pydantic import BaseModel


class AuditRecord(BaseModel):
    id: str
    action: str
    entity_type: str
    entity_id: str
    user_id: str
    ip_address: Optional[str] = None
    status: str
    created_at: str
    sequence: int
    chain_hash: str
    details: dict[str, Any] = {}


class AuditChainIntegrity(BaseModel):
    is_valid: bool
    message: Optional[str] = None
    total_records: int


class AuditResponse(BaseModel):
    company_id: str
    records: list[AuditRecord]
    chain_integrity: AuditChainIntegrity
