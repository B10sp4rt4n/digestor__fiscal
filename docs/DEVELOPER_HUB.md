# Developer Hub

Esta API esta construida como REST sobre FastAPI.

## URLs de documentacion
- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI: `/openapi.json`
- Portal developer: `/developer`
- Quickstart API: `/developer/quickstart`

## Sandbox (probar antes de pagar)

Puedes crear una cuenta de prueba con este endpoint:

```bash
curl -X POST "http://localhost:8000/auth/sandbox/signup" \
  -H "Content-Type: application/json" \
  -d '{"username":"mi_usuario_demo"}'
```

Respuesta esperada:

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 86400,
  "username": "mi_usuario_demo",
  "role": "operator",
  "tenant_id": "sandbox-mi_usuario_demo",
  "docs_url": "/docs",
  "openapi_url": "/openapi.json"
}
```

## Primer request autenticado

```bash
curl -X GET "http://localhost:8000/health" \
  -H "Authorization: Bearer TU_TOKEN"
```

## Subir primer documento

```bash
curl -X POST "http://localhost:8000/v1/documents" \
  -H "Authorization: Bearer TU_TOKEN" \
  -F "file=@./mi_csf.pdf" \
  -F "document_type=csf"
```

## SDKs base en este repositorio
- Python: `/sdk/python/digestor_sdk.py`
- TypeScript: `/sdk/typescript/client.ts`

## Nota de publicacion
Si quieres documentacion publica en internet, publica `/docs` y este markdown en un dominio externo (por ejemplo, docs.tu-dominio.com) y conecta analitica de adquisicion.
