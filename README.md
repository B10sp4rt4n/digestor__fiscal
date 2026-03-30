# Digestor Fiscal — CSF → Hashes → Autollenado → Telemetría

Arquitectura **cloud-first** con **modo survival (offline-first)**.
- **Neon/PostgreSQL** como fuente canónica recomendada.
- **SQLite** local para staging/edge y modo offline.
- API en **FastAPI** con endpoints de carga (`/upload/pdf`, `/upload/zip`), sincronización (`/sync/batch`, `/sync/changes`) y salud (`/health`).

## ▶️ Ejecutar con Neon (recomendado)
```bash
cp .env.example .env
# Ajusta DB_URL con tu connection string de Neon
# Ejemplo:
# DB_URL=postgresql+psycopg://USER:PASSWORD@EP-XXXX.us-east-1.aws.neon.tech/neondb?sslmode=require
hypercorn app.main:app --reload --bind 0.0.0.0:8000
```

Notas:
- Si usas Neon, deja `USE_SQLITE=0`.
- La app fuerza `sslmode=require` cuando detecta PostgreSQL y la URL no lo trae explícito.
- Se activa `pool_pre_ping` para reducir errores por conexiones dormidas.

## ▶️ Ejecutar en local (SQLite)
```bash
python -m venv .venv && . .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Para usar SQLite local (modo dev/edge):
#   DB_URL=sqlite:///./local.db   USE_SQLITE=1
hypercorn app.main:app --reload --bind 0.0.0.0:8000
```

## ▶️ Ejecutar con Postgres (Docker)
```bash
docker compose up -d postgres
# Ajusta .env a DB_URL=postgresql+psycopg://postgres:postgres@localhost:5432/digestor
hypercorn app.main:app --reload --bind 0.0.0.0:8000
```

## Variables .env
- `DB_URL` → conexión principal a BD (sqlite o postgres)
- `DATABASE_URL` → alias estándar de despliegue; si existe, tiene prioridad sobre `DB_URL`
- `USE_SQLITE` → `1` para habilitar SQLite local (staging/edge)
- `DB_SSL_REQUIRE` → exige SSL para PostgreSQL/Neon
- `DB_POOL_PRE_PING` → valida conexiones antes de usarlas
- `DB_POOL_RECYCLE_SECONDS` → recicla conexiones largas
- `API_KEY` → clave HMAC para firmar eventos de survival
- `TENANT_ID` → id de empresa (tenant) por defecto en dev
- `SURVIVAL_ENABLED` → `1` para activar cola offline
- `UPLOAD_DIR` → carpeta donde se guardan PDFs/ZIPs
- `LOCAL_BACKUP_ENABLED` → habilita exportación local en JSON
- `LOCAL_BACKUP_DIR` → carpeta donde se guardan los respaldos
- `LOCAL_BACKUP_INCLUDE_USERS` → incluye tabla de usuarios en el respaldo

## Endpoints
- `POST /upload/pdf` → sube 1 CSF (PDF)
- `POST /upload/zip` → sube ZIP con múltiples CSFs
- `POST /sync/batch` → recibe eventos/filas desde edge (idempotente)
- `GET  /sync/changes` → devuelve cambios desde `since`
- `POST /admin/backups/export` → genera respaldo local JSON del tenant actual
- `GET  /health` → estado simple
- `GET  /version` → versión del servicio

## Developer API (developer-led)

La API es REST y expone OpenAPI automaticamente.

- `GET /docs` → Swagger UI
- `GET /redoc` → ReDoc
- `GET /openapi.json` → especificacion OpenAPI
- `GET /developer` → portal developer (metadatos y links)
- `GET /developer/quickstart` → pasos de onboarding por API

### Sandbox: probar antes de pagar

Puedes crear cuenta de prueba sin intervención comercial:

```bash
curl -X POST "http://localhost:8000/auth/sandbox/signup" \
	-H "Content-Type: application/json" \
	-d '{"username":"demo_dev"}'
```

La respuesta incluye `access_token` y `tenant_id` de sandbox aislado.

### SDKs base incluidos
- Python: `sdk/python/digestor_sdk.py`
- TypeScript: `sdk/typescript/client.ts`

### Guía rápida completa
- Ver `docs/DEVELOPER_HUB.md`

## Portal público en Netlify

Este repo ya incluye un portal estático en `developer-portal/` y configuración en `netlify.toml`.

### Deploy
1. Conecta este repo en Netlify.
2. Netlify detectará `netlify.toml` y publicará `developer-portal/`.
3. Abre tu sitio con `?apiBase=https://TU_API_PUBLICA` para apuntar a tu backend real.

Ejemplo:
`https://tu-portal.netlify.app/?apiBase=https://api.tu-dominio.com`

## Deploy API en Railway

Este repo incluye `Procfile` para Railway:

- `web: hypercorn app.main:app --bind 0.0.0.0:${PORT:-8000}`

Variables mínimas recomendadas en Railway:
- `DATABASE_URL` (Neon)
- `DB_SSL_REQUIRE=1`
- `AUTH_CONNECTOR=jwt`
- `AUTH_ALLOW_ANONYMOUS=0`
- `JWT_SECRET_KEY` (largo y privado)
- `SEED_ADMIN_USERNAME`
- `SEED_ADMIN_PASSWORD`
- `SEED_ADMIN_TENANT`

Healthcheck sugerido:
- `/health`

## Estructura básica
- `app/core/config.py` → settings y DB toggle
- `app/db/session.py` → sesión SQLAlchemy
- `app/db/base.py` → Base declarativa
- `app/models/*` → modelos SQLAlchemy (csf, sucursal, usuario, evento)
- `app/schemas/*` → Pydantic DTOs
- `app/services/survival_engine.py` → cola offline y sync
- `app/services/ingest.py` → stub de extracción de PDF/ZIP
- `app/api/routers/*` → rutas FastAPI

## Nota
Este es un **scaffold** listo para iterar; agrega Alembic/migraciones con:
```bash
alembic init migrations
alembic revision --autogenerate -m "init"
alembic upgrade head
```

## Contrato operativo (SLA/SLO + Integraciones)
Para alinear carga masiva, correccion por evento y salida a CRM/ERP:
- Ver `CONTRATO_OPERATIVO_V1.md`


---
## Alembic (migraciones) — comandos rápidos
```bash
# Usa DB_URL desde tu .env (SQLite por defecto)
export DB_URL=sqlite:///./local.db  # o tu URL de Postgres
alembic upgrade head
```

## Seed de datos demo
```bash
python -m app.main  # arranca API (opcional para crear tablas en dev)
python seeds/seed_demo.py
```


---
## Makefile (atajos)
```bash
make install
make migrate
make seed
make run
```

## Dashboard (Streamlit)
```bash
# Usa la misma DB_URL del .env
streamlit run upload_ui.py
```

El dashboard ya está integrado en la misma UI de carga e historial.
Si prefieres mantener el comando anterior, `streamlit run streamlit_app.py` sigue funcionando como alias.

### Respaldo local en PC
- Los usuarios con rol `admin` o `superadmin` pueden generar un respaldo desde el sidebar de Streamlit.
- El archivo se escribe en `LOCAL_BACKUP_DIR` y contiene export JSON por tabla, filtrado por tenant.
- Esto permite operar con Neon como base principal y mantener una copia local exportable en la PC del usuario.

### psql con Neon
Puedes validar la conexión directa con un comando como este:

```bash
psql 'postgresql://neondb_owner:TU_PASSWORD@ep-XXXX.us-east-1.aws.neon.tech/neondb?sslmode=require'
```

Si prefieres no guardar la URL en `DB_URL`, puedes exportarla como `DATABASE_URL` y la aplicación la usará igual.

### Validación online del QR del SAT
- Activa en `.env` con `SAT_ONLINE_VALIDATION=1`.
- Ajusta `SAT_TIMEOUT`, `SAT_RETRIES` y `SAT_CACHE_TTL`.
- El endpoint `POST /upload/pdf` acepta `qr_text` y devuelve `qr_valid` (formato local) y `qr_online` (intento de verificación HTTP).

> Nota: La verificación HTTP **no sustituye** las validaciones fiscales completas. Úsala como chequeo de *reachability* y soporte operativo.


### Notificaciones de alertas
Configura en `.env`:
```
ALERTS_NOTIFY_SLACK_WEBHOOK=https://hooks.slack.com/services/...
ALERTS_NOTIFY_WEBHOOK=https://mi-servicio-webhook/alerta
ALERTS_AUTOPOLL=1
ALERTS_AUTOPOLL_SECONDS=60
```
- Enviar manual: `POST /telemetry/alerts/notify?minutes=60`
- Autopoll en background: habilita `ALERTS_AUTOPOLL=1` (envía cuando haya alertas activas).
