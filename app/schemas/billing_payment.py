from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RegisterPayment(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    idempotency_key: UUID
    reference: str = Field(min_length=1, max_length=100, pattern=r"^[^|\x00-\x1f]+$")
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    paid_at: datetime
    payment_form: Literal['01', '02', '03', '04', '28']

    @field_validator('paid_at')
    @classmethod
    def offset_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Fecha de pago requiere zona horaria explícita')
        return value

    @field_validator('reference')
    @classmethod
    def reference_normalized(cls, value):
        return ' '.join(value.upper().split())
