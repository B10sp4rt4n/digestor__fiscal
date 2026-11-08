from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, model_validator


class CancelDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    idempotency_key: UUID
    reason: Literal['01', '02', '03']
    replacement_draft_id: UUID | None = None

    @model_validator(mode='after')
    def replacement_required(self):
        if (self.reason == '01') != (self.replacement_draft_id is not None):
            raise ValueError('Motivo 01 requiere factura sustituta; otros motivos no la admiten')
        return self


class RecoverReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    receipt_uuid: UUID
