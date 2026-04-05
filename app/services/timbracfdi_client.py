import base64
from datetime import datetime, timedelta, timezone
from textwrap import dedent
from typing import Any

import requests

from app.core.config import settings


class TimbraCFDIConfigurationError(RuntimeError):
    """Raised when TimbraCFDI settings are missing."""


def is_configured() -> bool:
    return bool(settings.timbracfdi_active_base_url and settings.timbracfdi_active_token)


def _require_configured() -> None:
    if not is_configured():
        raise TimbraCFDIConfigurationError(
            "Configura las variables activas de TimbraCFDI para el entorno actual (`TIMBRACFDI_ENV`)."
        )


def _headers() -> dict[str, str]:
    _require_configured()
    return {
        "Authorization": f"Bearer {settings.timbracfdi_active_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return settings.timbracfdi_active_base_url.rstrip("/")


def _normalize_response(response: requests.Response) -> dict[str, Any]:
    try:
        data: Any = response.json()
    except ValueError:
        data = response.text[:5000]

    return {
        "ok": response.ok,
        "status_code": response.status_code,
        "provider_response": data,
    }


_DEMO_CFDI_XML_TEMPLATE = dedent(
    """\
    <?xml version="1.0" encoding="utf-8"?>
    <cfdi:Comprobante xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Moneda="MXN" Total="560.00" xsi:schemaLocation="http://www.sat.gob.mx/cfd/4 http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd" Exportacion="01" MetodoPago="PUE" TipoDeComprobante="I" SubTotal="500.00" FormaPago="01" LugarExpedicion="32690" Fecha="{fecha}" Folio="{folio}" Version="4.0">
      <cfdi:Emisor Rfc="IIA040805DZ4" Nombre="INDISTRIA ILUMINADORA DE ALMACENES" RegimenFiscal="626" />
      <cfdi:Receptor Rfc="EKU9003173C9" Nombre="ESCUELA KEMPER URGATE" DomicilioFiscalReceptor="42501" RegimenFiscalReceptor="603" UsoCFDI="G03" />
      <cfdi:Conceptos>
        <cfdi:Concepto ClaveProdServ="78101800" NoIdentificacion="123" Cantidad="1" ClaveUnidad="E48" Descripcion="Producto Demo Digestor" ValorUnitario="500.00" Importe="500.00" ObjetoImp="02">
          <cfdi:Impuestos>
            <cfdi:Traslados>
              <cfdi:Traslado Base="500.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="80.00" />
            </cfdi:Traslados>
            <cfdi:Retenciones>
              <cfdi:Retencion Base="500.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.040000" Importe="20.00" />
            </cfdi:Retenciones>
          </cfdi:Impuestos>
        </cfdi:Concepto>
      </cfdi:Conceptos>
      <cfdi:Impuestos TotalImpuestosRetenidos="20.00" TotalImpuestosTrasladados="80.00">
        <cfdi:Retenciones>
          <cfdi:Retencion Impuesto="002" Importe="20.00" />
        </cfdi:Retenciones>
        <cfdi:Traslados>
          <cfdi:Traslado Base="500.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="80.00" />
        </cfdi:Traslados>
      </cfdi:Impuestos>
    </cfdi:Comprobante>
    """
).strip()


def build_demo_cfdi_xml(folio: str | None = None) -> str:
    issue_time = datetime.now(timezone.utc) - timedelta(hours=settings.TIMBRACFDI_EMIT_OFFSET_HOURS)
    fecha = issue_time.strftime("%Y-%m-%dT%H:%M:%S")
    folio_value = folio or str(int(issue_time.timestamp()))
    return _DEMO_CFDI_XML_TEMPLATE.format(fecha=fecha, folio=folio_value)


def build_demo_cfdi_xml_base64(folio: str | None = None) -> str:
    xml = build_demo_cfdi_xml(folio=folio)
    return base64.b64encode(xml.encode("utf-8")).decode("ascii")


def timbra_demo_cfdi(folio: str | None = None, id_comprobante: str | None = None) -> dict[str, Any]:
    if settings.timbracfdi_environment == "production" and not settings.TIMBRACFDI_DEMO_ENABLED:
        raise TimbraCFDIConfigurationError(
            "El timbrado demo está deshabilitado en modo productivo. Usa `/timbracfdi/timbra` con tu XML real."
        )

    folio_value = folio or str(int((datetime.now(timezone.utc) - timedelta(hours=settings.TIMBRACFDI_EMIT_OFFSET_HOURS)).timestamp()))
    xml_base64 = build_demo_cfdi_xml_base64(folio=folio_value)
    support_id = id_comprobante or f"digestor-demo-{folio_value}"
    return timbra_cfdi(xml_base64=xml_base64, id_comprobante=support_id)


def ping() -> dict[str, Any]:
    """Connectivity/auth probe against the documented timbrado endpoint.

    We intentionally send an empty payload so the provider returns a structured
    response without needing a real XML CFDI. This is enough to validate reachability
    and that the bearer token is being accepted by the gateway.
    """
    response = requests.post(
        f"{_base_url()}/Timbrado/TimbraCFDI",
        headers=_headers(),
        json={},
        timeout=settings.TIMBRACFDI_TIMEOUT,
    )
    return _normalize_response(response)


def registra_emisor(
    rfc_emisor: str,
    base64_cer: str,
    base64_key: str,
    contrasena: str,
) -> dict[str, Any]:
    payload = {
        "RfcEmisor": rfc_emisor,
        "Base64Cer": base64_cer,
        "Base64Key": base64_key,
        "Contrasena": contrasena,
    }
    response = requests.post(
        f"{_base_url()}/Timbrado/RegistraEmisor",
        headers=_headers(),
        json=payload,
        timeout=settings.TIMBRACFDI_TIMEOUT,
    )
    return _normalize_response(response)


def timbra_cfdi(xml_base64: str, id_comprobante: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"XmlComprobanteBase64": xml_base64}
    if id_comprobante:
        payload["IdComprobante"] = id_comprobante

    response = requests.post(
        f"{_base_url()}/Timbrado/TimbraCFDI",
        headers=_headers(),
        json=payload,
        timeout=settings.TIMBRACFDI_TIMEOUT,
    )
    return _normalize_response(response)
