from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProductCreateRequest(BaseModel):
    company_id: str | None = Field(default=None, description="Tenant opcional; normalmente se toma del JWT.")
    sku: str = Field(description="Clave interna única por tenant.")
    name: str = Field(description="Nombre visible del producto o servicio.")
    description: str | None = None
    sat_product_code: str = Field(default="78101800", description="Clave SAT del producto/servicio.")
    unit_code: str = Field(default="E48", description="Clave SAT de unidad.")
    unit_name: str | None = Field(default="Unidad de servicio", description="Texto libre de unidad.")
    price: float = Field(default=0.0, ge=0)
    currency: str = Field(default="MXN")
    tax_rate: float = Field(default=0.16, ge=0)
    tax_object: str = Field(default="02", description="01=no objeto, 02=sí objeto de impuesto.")
    is_active: bool = True

    model_config = {
        "json_schema_extra": {
            "example": {
                "sku": "CONS-001",
                "name": "Consultoría mensual",
                "description": "Servicio profesional recurrente",
                "sat_product_code": "80101500",
                "unit_code": "E48",
                "unit_name": "Servicio",
                "price": 2500.0,
                "currency": "MXN",
                "tax_rate": 0.16,
                "tax_object": "02",
                "is_active": True,
            }
        }
    }


class ProductUpdateRequest(BaseModel):
    sku: str | None = None
    name: str | None = None
    description: str | None = None
    sat_product_code: str | None = None
    unit_code: str | None = None
    unit_name: str | None = None
    price: float | None = Field(default=None, ge=0)
    currency: str | None = None
    tax_rate: float | None = Field(default=None, ge=0)
    tax_object: str | None = None
    is_active: bool | None = None


class ProductResponse(BaseModel):
    id: str
    company_id: str
    sku: str
    name: str
    description: str | None = None
    sat_product_code: str
    unit_code: str
    unit_name: str | None = None
    price: float
    currency: str
    tax_rate: float
    tax_object: str
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DraftItemInput(BaseModel):
    product_id: str | None = Field(default=None, description="Si lo envías, la API toma defaults del catálogo.")
    sku: str | None = None
    description: str | None = None
    sat_product_code: str | None = None
    unit_code: str | None = None
    quantity: float = Field(default=1.0, gt=0)
    unit_price: float | None = Field(default=None, ge=0)
    tax_rate: float | None = Field(default=None, ge=0)
    tax_object: str | None = None


class DraftItemResponse(BaseModel):
    id: str
    product_id: str | None = None
    sku: str | None = None
    description: str
    sat_product_code: str
    unit_code: str
    quantity: float
    unit_price: float
    tax_rate: float
    tax_object: str
    line_subtotal: float
    line_taxes: float
    line_total: float


class BillingDraftCreateRequest(BaseModel):
    company_id: str | None = Field(default=None, description="Tenant opcional; normalmente se toma del JWT.")
    customer_name: str | None = Field(default=None, description="Razón social o nombre del receptor.")
    customer_rfc: str | None = None
    customer_zip: str | None = None
    customer_regimen: str | None = None
    customer_use_cfdi: str | None = Field(default="G03")
    emitter_rfc: str | None = Field(default=None, description="Opcional; si no se envía, usa un emisor demo sandbox.")
    emitter_name: str | None = None
    emitter_regimen: str | None = None
    place_of_issue: str | None = None
    currency: str = "MXN"
    payment_method: str = "PUE"
    payment_form: str = "01"
    series: str | None = "PF"
    folio: str | None = None
    notes: str | None = None
    items: list[DraftItemInput] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "customer_name": "CLIENTE DEMO SA DE CV",
                "customer_rfc": "XAXX010101000",
                "customer_zip": "64000",
                "customer_regimen": "601",
                "customer_use_cfdi": "G03",
                "notes": "Prefactura lista para revisión",
                "items": [
                    {
                        "description": "Consultoría estratégica",
                        "sat_product_code": "80101500",
                        "unit_code": "E48",
                        "quantity": 1,
                        "unit_price": 2500.0,
                        "tax_rate": 0.16,
                        "tax_object": "02"
                    }
                ]
            }
        }
    }


class BillingDraftUpdateRequest(BaseModel):
    customer_name: str | None = None
    customer_rfc: str | None = None
    customer_zip: str | None = None
    customer_regimen: str | None = None
    customer_use_cfdi: str | None = None
    emitter_rfc: str | None = None
    emitter_name: str | None = None
    emitter_regimen: str | None = None
    place_of_issue: str | None = None
    currency: str | None = None
    payment_method: str | None = None
    payment_form: str | None = None
    series: str | None = None
    folio: str | None = None
    notes: str | None = None
    items: list[DraftItemInput] | None = Field(default=None, description="Si lo envías, reemplaza el detalle actual.")


class BillingDraftResponse(BaseModel):
    id: str
    company_id: str
    status: str
    stamp_status: str
    customer_name: str | None = None
    customer_rfc: str | None = None
    customer_zip: str | None = None
    customer_regimen: str | None = None
    customer_use_cfdi: str | None = None
    emitter_rfc: str | None = None
    emitter_name: str | None = None
    emitter_regimen: str | None = None
    place_of_issue: str | None = None
    currency: str
    payment_method: str
    payment_form: str
    series: str | None = None
    folio: str | None = None
    notes: str | None = None
    subtotal: float
    taxes: float
    total: float
    ready_to_stamp: bool
    missing_fields: list[str] = Field(default_factory=list)
    items: list[DraftItemResponse] = Field(default_factory=list)
    stamped_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BillingDraftStampRequest(BaseModel):
    id_comprobante: str | None = Field(default=None, description="Id opcional para soporte/trace en el PAC.")

    model_config = {
        "json_schema_extra": {
            "example": {}
        }
    }


class BillingDraftStampResponse(BaseModel):
    draft: BillingDraftResponse
    ok: bool
    remote_status_code: int
    provider_response: dict[str, Any] | list[Any] | str | None = None
    xml_base64: str
