from typing import Any

from pydantic import BaseModel, Field


class BillingProviderStatusResponse(BaseModel):
    provider: str = "timbracfdi"
    configured: bool
    base_url: str
    token_present: bool
    probe_attempted: bool = False
    reachable: bool | None = None
    auth_accepted: bool | None = None
    remote_status_code: int | None = None
    provider_response: dict[str, Any] | list[Any] | str | None = None


class TimbradoRequest(BaseModel):
    xml_base64: str = Field(description="XML CFDI codificado en Base64")
    id_comprobante: str | None = Field(default=None, description="Id interno opcional para soporte")


class TimbradoDemoRequest(BaseModel):
    folio: str | None = Field(default=None, description="Folio opcional para el CFDI demo")
    id_comprobante: str | None = Field(default=None, description="Id interno opcional para soporte")


class RegistraEmisorRequest(BaseModel):
    rfc_emisor: str
    base64_cer: str
    base64_key: str
    contrasena: str


class BillingProviderProxyResponse(BaseModel):
    provider: str = "timbracfdi"
    ok: bool
    remote_status_code: int
    provider_response: dict[str, Any] | list[Any] | str | None = None
