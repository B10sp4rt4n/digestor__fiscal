from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_NUMBER = 999_999_999_999


class InvoiceNumberingInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    prefix: str = Field(max_length=25, pattern=r'^[A-Z0-9-]*$')
    include_year: bool
    reset_annually: bool
    digits: int = Field(ge=1, le=12)
    next_number: int = Field(ge=1, le=MAX_NUMBER)
    expected_revision: int = Field(ge=0)
    declared_actor: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode='after')
    def valid_series(self):
        if self.reset_annually and not self.include_year:
            raise ValueError('El reinicio anual requiere incluir el año en la serie')
        if self.include_year and len(self.prefix) > 20:
            raise ValueError('La serie con año admite un prefijo de hasta 20 caracteres')
        return self
