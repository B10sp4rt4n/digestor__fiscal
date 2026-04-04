from typing import Any

import requests

from app.core.config import settings


class TimbraCFDIConfigurationError(RuntimeError):
    """Raised when TimbraCFDI settings are missing."""


def is_configured() -> bool:
    return bool((settings.TIMBRACFDI_BASE_URL or "").strip() and (settings.TIMBRACFDI_TOKEN or "").strip())


def _require_configured() -> None:
    if not is_configured():
        raise TimbraCFDIConfigurationError(
            "Configura TIMBRACFDI_BASE_URL y TIMBRACFDI_TOKEN para usar el proveedor de timbrado."
        )


def _headers() -> dict[str, str]:
    _require_configured()
    return {
        "Authorization": f"Bearer {settings.TIMBRACFDI_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return settings.TIMBRACFDI_BASE_URL.rstrip("/")


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
