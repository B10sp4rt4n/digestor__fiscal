from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FiscalChanges(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    customer_name: str | None = Field(default=None, min_length=1, max_length=200, pattern=r'^[^|\x00-\x1f]+$')
    customer_zip: str | None = Field(default=None, pattern=r'^\d{5}$')
    customer_regimen: str | None = Field(default=None, pattern=r'^\d{3}$')
    customer_use_cfdi: str | None = Field(default=None, pattern=r'^[GIDS]\d{2}$')
    payment_method: Literal['PUE', 'PPD'] | None = None
    payment_form: Literal['01', '02', '03', '04', '28', '99'] | None = None
    operation_reference: str | None = Field(default=None, min_length=1, max_length=100, pattern=r'^[^|\x00-\x1f]+$')


class CreateFiscalRevision(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    idempotency_key: UUID
    source_uuid: UUID
    reason: str = Field(min_length=5, max_length=500, pattern=r'^[^\x00-\x1f]+$')
    changes: FiscalChanges

    @model_validator(mode='after')
    def actual_changes(self):
        if not self.changes.model_dump(exclude_none=True): raise ValueError('Indica al menos un dato a corregir')
        return self
