from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/developer", tags=["developer"])


class DeveloperPortalResponse(BaseModel):
    service: str
    version: str
    api_style: str
    docs_url: str
    redoc_url: str
    openapi_url: str
    sandbox_signup_url: str
    quickstart_url: str
    sdk_python_path: str
    sdk_typescript_path: str


@router.get("", response_model=DeveloperPortalResponse)
def developer_portal():
    if not settings.DEVELOPER_PORTAL_ENABLED:
        raise HTTPException(status_code=404, detail="Developer portal no disponible.")

    return DeveloperPortalResponse(
        service="Digestor Fiscal API",
        version=settings.SERVICE_VERSION,
        api_style="REST",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        sandbox_signup_url="/auth/sandbox/signup",
        quickstart_url="/developer/quickstart",
        sdk_python_path="/sdk/python/digestor_sdk.py",
        sdk_typescript_path="/sdk/typescript/client.ts",
    )


@router.get("/quickstart")
def developer_quickstart():
    if not settings.DEVELOPER_PORTAL_ENABLED:
        raise HTTPException(status_code=404, detail="Developer portal no disponible.")

    return {
        "steps": [
            "POST /auth/sandbox/signup para crear cuenta de prueba y obtener Bearer token.",
            "Abre /docs para explorar y probar endpoints.",
            "Usa POST /v1/documents con Authorization: Bearer <token> para enviar tu primer documento.",
            "Consulta GET /v1/documents/{id} para revisar estado y resultados.",
        ]
    }