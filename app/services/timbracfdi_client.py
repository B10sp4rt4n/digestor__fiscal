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
      <cfdi:Emisor Rfc="IIA040805DZ4" Nombre="INDUSTRIA ILUMINADORA DE ALMACENES" RegimenFiscal="626" />
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


# Códigos SAT CFDI 4.0 que indican que el RFC del receptor NO existe en el padrón
_RFC_NOT_FOUND_CODES = {
    "CFDI40132",  # RFC receptor no se encuentra en el padrón
    "CFDI40133",  # RFC receptor no es válido para operaciones
    "CFDI40134",  # RFC receptor cancelado
    "CFDI40166",  # RFC receptor no localizado
}

# Códigos que indican error del EMISOR (no del receptor) → RFC receptor podría ser válido
_EMISOR_ERROR_CODES = {
    "CFDI40111",  # Emisor no registrado
    "CFDI40112",  # CSD emisor no vigente
    "CFDI40113",  # CSD emisor no corresponde
    "CFDI40114",  # Emisor cancelado
    "CFDI40139",  # Nombre emisor no coincide con RFC emisor
    "CFDI40140",  # CSD no pertenece al emisor
    "21001",      # Código genérico de error de emisor en algunos PACs
}

_PROBE_XML_TEMPLATE = dedent(
    """\
    <?xml version="1.0" encoding="utf-8"?>
    <cfdi:Comprobante xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:cfdi="http://www.sat.gob.mx/cfd/4" \
Moneda="MXN" Total="116.00" xsi:schemaLocation="http://www.sat.gob.mx/cfd/4 http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd" \
Exportacion="01" MetodoPago="PUE" TipoDeComprobante="I" SubTotal="100.00" FormaPago="01" \
LugarExpedicion="32690" Fecha="{fecha}" Folio="{folio}" Version="4.0">
      <cfdi:Emisor Rfc="IIA040805DZ4" Nombre="INDUSTRIA ILUMINADORA DE ALMACENES" RegimenFiscal="626" />
      <cfdi:Receptor Rfc="{rfc}" Nombre="{nombre}" DomicilioFiscalReceptor="{cp}" RegimenFiscalReceptor="{regimen}" UsoCFDI="G03" />
      <cfdi:Conceptos>
        <cfdi:Concepto ClaveProdServ="78101800" Cantidad="1" ClaveUnidad="E48" Descripcion="Prueba validacion RFC" ValorUnitario="100.00" Importe="100.00" ObjetoImp="02">
          <cfdi:Impuestos>
            <cfdi:Traslados>
              <cfdi:Traslado Base="100.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="16.00" />
            </cfdi:Traslados>
          </cfdi:Impuestos>
        </cfdi:Concepto>
      </cfdi:Conceptos>
      <cfdi:Impuestos TotalImpuestosTrasladados="16.00">
        <cfdi:Traslados>
          <cfdi:Traslado Base="100.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="16.00" />
        </cfdi:Traslados>
      </cfdi:Impuestos>
    </cfdi:Comprobante>
    """
).strip()


def validate_rfc_via_pac(
    rfc: str,
    nombre: str,
    cp: str,
    regimen: str,
) -> dict[str, Any]:
    """
    Envía un CFDI mínimo de prueba al sandbox del PAC para verificar si el RFC
    del receptor existe y está activo en el padrón del SAT.

    Retorna:
        {
            "rfc_valid": bool | None,   # None = no se pudo determinar
            "active_in_padron": bool | None,
            "pac_error_code": str | None,
            "pac_message": str | None,
            "pac_available": bool,
        }
    """
    if not is_configured():
        return {
            "rfc_valid": None,
            "active_in_padron": None,
            "pac_error_code": None,
            "pac_message": "PAC sandbox no configurado.",
            "pac_available": False,
        }

    issue_time = datetime.now(timezone.utc) - timedelta(hours=settings.TIMBRACFDI_EMIT_OFFSET_HOURS)
    fecha = issue_time.strftime("%Y-%m-%dT%H:%M:%S")
    folio = f"VAL-{int(issue_time.timestamp())}"

    xml = _PROBE_XML_TEMPLATE.format(
        fecha=fecha,
        folio=folio,
        rfc=rfc.upper().strip(),
        nombre=(nombre or rfc).upper().strip()[:254],
        cp=(cp or "32690").strip(),
        regimen=(regimen or "626").strip(),
    )
    xml_b64 = base64.b64encode(xml.encode("utf-8")).decode("ascii")

    try:
        resp = timbra_cfdi(xml_base64=xml_b64, id_comprobante=folio)
    except Exception as e:
        return {
            "rfc_valid": None,
            "active_in_padron": None,
            "pac_error_code": None,
            "pac_message": f"Error de conexión con el PAC: {e}",
            "pac_available": False,
        }

    pac_data = resp.get("provider_response", {})

    # Extraer código de error SAT del response — varios formatos posibles
    error_code: str | None = None
    error_msg: str | None = None
    if isinstance(pac_data, dict):
        # Formato 1: lista de errores en "Errores" o "errores"
        errores = pac_data.get("Errores") or pac_data.get("errores") or []
        if errores and isinstance(errores, list):
            first = errores[0]
            error_code = str(first.get("CodigoError") or first.get("CodigoSat") or first.get("codigo") or "").strip()
            error_msg = str(first.get("Descripcion") or first.get("Mensaje") or first.get("mensaje") or "").strip()
        # Formato 2: error en nivel raíz (CodigoSat / Mensaje)
        elif pac_data.get("CodigoSat") or pac_data.get("Codigo"):
            raw_code = str(pac_data.get("CodigoSat") or pac_data.get("Codigo") or "").strip()
            raw_msg = str(pac_data.get("Mensaje") or pac_data.get("mensaje") or "").strip()
            # El CodigoSat puede ser "21001" mientras que el código SAT real está en el Mensaje "CFDI40139 - ..."
            import re as _re
            m = _re.match(r"(CFDI\d+)", raw_msg)
            error_code = m.group(1) if m else raw_code
            error_msg = raw_msg
        elif not resp["ok"]:
            error_msg = str(pac_data.get("Mensaje") or pac_data.get("mensaje") or pac_data.get("message") or "")[:200]

    # Si timbró exitosamente → RFC definitivamente válido
    if resp["ok"] and not error_code:
        return {
            "rfc_valid": True,
            "active_in_padron": True,
            "pac_error_code": None,
            "pac_message": "RFC verificado y activo en el padrón del SAT.",
            "pac_available": True,
        }

    # RFC no encontrado en padrón
    if error_code in _RFC_NOT_FOUND_CODES:
        return {
            "rfc_valid": False,
            "active_in_padron": False,
            "pac_error_code": error_code,
            "pac_message": error_msg or f"RFC no encontrado en el padrón del SAT ({error_code}).",
            "pac_available": True,
        }

    # Error del emisor → no podemos determinar si el receptor es válido, pero el PAC respondió
    if error_code in _EMISOR_ERROR_CODES:
        return {
            "rfc_valid": None,
            "active_in_padron": None,
            "pac_error_code": error_code,
            "pac_message": "PAC respondió pero no se pudo verificar el receptor (error de configuración del emisor).",
            "pac_available": True,
        }

    # Otros errores SAT (estructura XML, régimen, etc.) → PAC está activo, RFC no fue el problema
    if error_code and error_code.startswith("CFDI"):
        return {
            "rfc_valid": None,
            "active_in_padron": None,
            "pac_error_code": error_code,
            "pac_message": f"PAC respondió con error de estructura ({error_code}): {error_msg}",
            "pac_available": True,
        }

    # Respuesta inesperada
    return {
        "rfc_valid": None,
        "active_in_padron": None,
        "pac_error_code": error_code,
        "pac_message": error_msg or "Respuesta inesperada del PAC.",
        "pac_available": True,
    }
