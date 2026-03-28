# Digestor Fiscal — CSF → Hashes → Autollenado → Telemetría

Arquitectura **cloud-first** con **modo survival (offline-first)**.
- **PostgreSQL** como fuente canónica.
- **SQLite** local para staging/edge y modo offline.
- API en **FastAPI** con endpoints de carga (`/upload/pdf`, `/upload/zip`), sincronización (`/sync/batch`, `/sync/changes`) y salud (`/health`).

## ▶️ Ejecutar en local (SQLite)
```bash
python -m venv .venv && . .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Para usar SQLite local (modo dev/edge):
#   DB_URL=sqlite:///./local.db   USE_SQLITE=1
uvicorn app.main:app --reload --port 8000
```

## ▶️ Ejecutar con Postgres (Docker)
```bash
docker compose up -d postgres
# Ajusta .env a DB_URL=postgresql+psycopg://postgres:postgres@localhost:5432/digestor
uvicorn app.main:app --reload --port 8000
```

## Variables .env
- `DB_URL` → conexión a BD (sqlite o postgres)
- `USE_SQLITE` → `1` para habilitar SQLite local (staging/edge)
- `API_KEY` → clave HMAC para firmar eventos de survival
- `TENANT_ID` → id de empresa (tenant) por defecto en dev
- `SURVIVAL_ENABLED` → `1` para activar cola offline
- `UPLOAD_DIR` → carpeta donde se guardan PDFs/ZIPs

## Endpoints
- `POST /upload/pdf` → sube 1 CSF (PDF)
- `POST /upload/zip` → sube ZIP con múltiples CSFs
- `POST /sync/batch` → recibe eventos/filas desde edge (idempotente)
- `GET  /sync/changes` → devuelve cambios desde `since`
- `GET  /health` → estado simple
- `GET  /version` → versión del servicio

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
