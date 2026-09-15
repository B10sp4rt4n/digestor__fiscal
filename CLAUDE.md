# CLAUDE.md — Digestor Fiscal

Contexto persistente para Claude Code. Actualizar ante cualquier decisión de arquitectura o corrección relevante.

---

## Propósito del proyecto

Sistema fiscal SaaS multi-tenant que:
1. Lee CSFs (Constancia de Situación Fiscal del SAT) en PDF.
2. Extrae datos fiscales (RFC, razón social, domicilio, régimen, CURP, idCIF, QR).
3. Los persiste en BD y los expone como payload CRM (autollenado).
4. Permite generar y timbrar CFDIs (facturas) contra un PAC (TimbracFDI33).
5. Expone telemetría, auditoría, métricas y portal developer.

Etapa actual: **beta / piloto** — el dueño lo usará él mismo primero.

---

## Stack

| Capa | Tecnología |
|---|---|
| API | FastAPI + Hypercorn |
| BD canónica | Neon / PostgreSQL (`psycopg`) |
| BD local / edge | SQLite (`USE_SQLITE=1`) |
| ORM / migraciones | SQLAlchemy + Alembic |
| Dashboard | Streamlit (`upload_ui.py`) |
| PDF parsing | PyMuPDF (`fitz`) + regex + Groq LLM fallback |
| QR decode | OpenCV (`cv2`) |
| Timbrado CFDI | PAC TimbracFDI33 (`app/services/timbracfdi_client.py`) |
| Auth | JWT (`app/core/auth.py`) |
| AI fallback | Groq (`llama-3.1-8b-instant`) + OpenAI opcional |

---

## Arquitectura

```
Cloud-first + modo survival (offline-first)
  ├── Neon/PostgreSQL  ← fuente canónica en producción
  └── SQLite local     ← staging / edge / desarrollo (USE_SQLITE=1)

Multi-tenant: cada registro lleva company_id / tenant_id.
Auth: JWT emitido por /auth/login. Roles: superadmin, admin, operator.
```

### Módulos clave

```
app/
  core/config.py        — Settings pydantic (lee .env)
  db/session.py         — SQLAlchemy session factory
  models/csf.py         — Modelo CSF (tabla "csf")
  models/usuario.py     — Usuarios y roles
  services/ingest.py    — Parser CSF PDF → datos (ver estado abajo)
  services/timbracfdi_client.py — Integración PAC
  services/survival_engine.py  — Cola offline / sync
  api/routers/upload.py — POST /upload/pdf y /upload/zip
  api/routers/sync.py   — /sync/batch y /sync/changes
```

---

## Estado de ingest.py (ACTUALIZADO)

**ingest.py NO es un stub** — está implementado con lógica real:

- `process_pdf(content, company_id)` → extrae todos los campos, retorna dict completo.
- `process_zip(content, company_id)` → itera PDFs dentro del ZIP.
- Parser híbrido: **fast path regex** segmentado por secciones + **fallback Groq LLM** cuando faltan RFC o razón social.
- `_extract_text_from_pdf()` usa **PyMuPDF (fitz)** (cambiado desde pypdf en commit `dc86acc`).
- `_extract_qr_from_pdf()` usa **OpenCV** para decodificar QR del PDF rasterizado.
- `_extract_colon_pairs()` maneja labels con valor en línea siguiente (tablas del SAT, fix commit `00435a8`).
- `_parse_csf_fields()` valida que el texto diga "CONSTANCIA DE SITUACIÓN FISCAL"; lanza `ValueError` si no.
- Geolocalización via INEGI oficial + fallback zippopotam (por CP).
- Corrección IA por campo: OpenAI como primario, Groq como fallback.

**Lo que sí puede fallar / áreas a monitorear:**
- El parser regex es frágil ante variaciones de layout del SAT (el PDF puede cambiar sin aviso).
- El fallback Groq requiere `GROQ_API_KEY` en `.env`; sin él el parser regex es el único.
- El QR decode con OpenCV puede fallar en PDFs escaneados de baja resolución.
- La validación online del QR al SAT usa TLS legacy fallback (`SAT_ALLOW_LEGACY_TLS=1`).

---

## Comandos clave

```bash
# Instalar dependencias
make install
# equivalente: python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

# Levantar API (SQLite local)
USE_SQLITE=1 DB_URL=sqlite:///./local.db make run
# equivalente: hypercorn app.main:app --reload --bind 0.0.0.0:8000

# Levantar API (Neon)
# Pon DATABASE_URL en .env y quita USE_SQLITE
make run

# Migraciones
make migrate
# equivalente: alembic upgrade head

# Seed demo
make seed
# equivalente: python seeds/seed_demo.py

# Tests
make test
# equivalente: pytest -q

# Dashboard Streamlit
streamlit run upload_ui.py

# Todo junto (dev)
make dev  # install + migrate + seed + run
```

---

## Variables .env importantes

```
DB_URL=sqlite:///./local.db     # o postgresql+psycopg://...
DATABASE_URL=...                # tiene prioridad sobre DB_URL si existe
USE_SQLITE=1                    # habilitar SQLite
TENANT_ID=demo-company
API_KEY=change-me-please
JWT_SECRET_KEY=...              # secreto largo en producción

# Timbrado
TIMBRACFDI_ENV=sandbox          # sandbox | production
TIMBRACFDI_SANDBOX_TOKEN=...
TIMBRACFDI_SANDBOX_BASE_URL=https://pruebas.timbracfdi33.mx:1444/api/v2

# AI (opcionales)
GROQ_API_KEY=...
OPENAI_API_KEY=...

# Auth
AUTH_CONNECTOR=jwt              # none|header|jwt
AUTH_ALLOW_ANONYMOUS=0
```

---

## Estado del repo (al retomar sesión 2026-09-14)

- Último commit: `f6dcb06` — security: ignorar extensiones de credenciales CSD/FIEL
- Branch: `main`, limpio (sin cambios sin commitear).
- Corriendo en **modo local SQLite** (`USE_SQLITE=1`).
- Pendiente: reconectar a Neon cuando se recupere la connection string.
- PAC TimbracFDI33 en modo **sandbox**; producción pendiente de activar.

---

## Decisiones de arquitectura registradas

| Fecha | Decisión |
|---|---|
| Pre-2026-09 | PDF extractor cambiado de `pypdf` → `fitz` (PyMuPDF): pypdf concatenaba palabras sin espacios. |
| Pre-2026-09 | `TIMBRACFDI_EMIT_OFFSET_HOURS` corregido 5→6: PAC usa CST UTC-6, no CDT. |
| Pre-2026-09 | Guardar XML timbrado real del PAC, no el XML pre-timbrado (fix serialización). |
| Pre-2026-09 | Parser fechas en español ("DD DE MES DE YYYY") y tablas SAT con valor en línea siguiente. |
| Pre-2026-09 | Ignorar extensiones de credenciales CSD/FIEL en uploads ZIP (.key, .cer, .pfx). |

---

## Notas de desarrollo

- El modelo `CSF` usa `JSONB` (PostgreSQL) para `crm_autofill`, `ai_field_corrections`, `corrected_json`, `field_validation`. En SQLite esto se serializa como TEXT por SQLAlchemy.
- Las migraciones Alembic están en `migrations/versions/`. Solo hay una versión: `20260327_01_add_csf_metadata_fields.py`.
- Los roles de usuario son: `superadmin`, `admin`, `operator`. El seed crea un admin por defecto.
- El portal developer en `developer-portal/` es estático (Netlify). El backend expone `/developer` y `/developer/quickstart`.
- SDKs de ejemplo: `sdk/python/digestor_sdk.py` y `sdk/typescript/client.ts`.
