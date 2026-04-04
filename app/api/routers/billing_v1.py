from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import SecurityContext, role_guard
from app.core.config import settings
from app.schemas.billing import (
    BillingProviderProxyResponse,
    BillingProviderStatusResponse,
    RegistraEmisorRequest,
    TimbradoDemoRequest,
    TimbradoRequest,
)
from app.services import timbracfdi_client
from app.services.timbracfdi_client import TimbraCFDIConfigurationError

router = APIRouter(prefix="/v1/billing", tags=["billing-v1"])


@router.get(
    "/provider/status",
    response_model=BillingProviderStatusResponse,
    summary="Verificar conexión con proveedor de timbrado",
    description="Confirma si TimbraCFDI está configurado y, si `probe=true`, intenta una llamada real al sandbox.",
)
def get_billing_provider_status(
    probe: bool = Query(default=True, description="Si true, intenta una llamada real al sandbox del proveedor."),
    ctx: SecurityContext = Depends(role_guard("admin", "superadmin")),
):
    configured = timbracfdi_client.is_configured()
    token_present = bool((settings.TIMBRACFDI_TOKEN or "").strip())

    response = BillingProviderStatusResponse(
        configured=configured,
        base_url=settings.TIMBRACFDI_BASE_URL,
        token_present=token_present,
        probe_attempted=probe and configured,
    )

    if not probe or not configured:
        return response

    try:
        result = timbracfdi_client.ping()
    except TimbraCFDIConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        response.reachable = False
        response.auth_accepted = False
        response.provider_response = {"error": str(exc)}
        return response

    response.reachable = True
    response.auth_accepted = result["status_code"] != 401
    response.remote_status_code = result["status_code"]
    response.provider_response = result["provider_response"]
    return response


@router.post(
    "/timbracfdi/timbra",
    response_model=BillingProviderProxyResponse,
    summary="Timbrar CFDI enviando XML en Base64",
    description="Usa este endpoint cuando ya tengas tu XML CFDI armado y solo quieras enviarlo al PAC de pruebas.",
)
def timbra_cfdi(
    body: TimbradoRequest,
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
):
    try:
        result = timbracfdi_client.timbra_cfdi(body.xml_base64, body.id_comprobante)
    except TimbraCFDIConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No fue posible conectar con TimbraCFDI: {exc}") from exc

    return BillingProviderProxyResponse(
        ok=result["ok"],
        remote_status_code=result["status_code"],
        provider_response=result["provider_response"],
    )


@router.post(
    "/timbracfdi/timbra-demo",
    response_model=BillingProviderProxyResponse,
    summary="Timbrado demo sandbox listo para Swagger",
    description="Genera internamente un CFDI demo válido para pruebas y lo timbra en el sandbox de TimbraCFDI. Puedes mandar `{}`.",
)
def timbra_demo_cfdi(
    body: TimbradoDemoRequest,
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
):
    try:
        result = timbracfdi_client.timbra_demo_cfdi(body.folio, body.id_comprobante)
    except TimbraCFDIConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No fue posible ejecutar el demo de timbrado: {exc}") from exc

    return BillingProviderProxyResponse(
        ok=result["ok"],
        remote_status_code=result["status_code"],
        provider_response=result["provider_response"],
    )


@router.post(
    "/timbracfdi/registra-emisor",
    response_model=BillingProviderProxyResponse,
    summary="Registrar emisor ante el PAC de pruebas",
    description="Registra o actualiza el emisor usando RFC, certificado `.cer`, llave `.key` y contraseña en Base64.",
)
def registra_emisor(
    body: RegistraEmisorRequest,
    ctx: SecurityContext = Depends(role_guard("admin", "superadmin")),
):
    try:
        result = timbracfdi_client.registra_emisor(
            rfc_emisor=body.rfc_emisor,
            base64_cer=body.base64_cer,
            base64_key=body.base64_key,
            contrasena=body.contrasena,
        )
    except TimbraCFDIConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No fue posible conectar con TimbraCFDI: {exc}") from exc

    return BillingProviderProxyResponse(
        ok=result["ok"],
        remote_status_code=result["status_code"],
        provider_response=result["provider_response"],
    )
