from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel


class EventoBase(BaseModel):
    company_id: str
    subtotal: float = 0.0
    impuestos: float = 0.0
    total: float = 0.0
    metodo_pago: Optional[str] = None
    forma_pago: Optional[str] = None


class EventoCreate(EventoBase):
    csf_id: Optional[str] = None
    sucursal_id: Optional[str] = None
    usuario_id: Optional[str] = None
    evt_hash: str
    timestamp: Optional[datetime] = None


class EventoOut(EventoBase):
    id: str
    evt_hash: str
    timestamp: datetime
    csf_id: Optional[str] = None
    sucursal_id: Optional[str] = None
    usuario_id: Optional[str] = None

    model_config = {"from_attributes": True}


class SyncBatchRequest(BaseModel):
    events: List[Dict[str, Any]]
    signature: Optional[str] = None


class SyncBatchResult(BaseModel):
    inserted: int
    skipped: int
    errors: int


class SyncChangesResult(BaseModel):
    events: List[Dict[str, Any]]
    total: int
