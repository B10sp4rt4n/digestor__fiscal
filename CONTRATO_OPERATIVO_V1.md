# Contrato Operativo V1 (Ingesta, Correccion por Evento y Sync Externo)

## 1) Objetivo
Definir un contrato unico para:
- Carga masiva de documentos.
- Correccion por evento (campo/documento).
- Entrega hacia CRM u otros sistemas.

Este contrato sirve como base para SLAs/SLOs y para negociacion con clientes enterprise.

## 2) Estado Actual vs Estado Objetivo

### Estado actual implementado
- POST /v1/documents: crea job y responde queued.
- GET /v1/documents/{job_id}: consulta estado del job.
- Upload/consulta CSF con field_validation campo por campo.
- Audit trail inmutable con cadena hash.

### Estado objetivo inmediato
- Aprobar documento para salida externa (gate de calidad).
- Publicar evento de sincronizacion con idempotencia.
- Entregar payload normalizado estable con version de contrato.

## 3) Entidades Canonicas

### 3.1 DocumentJob
- job_id: string (uuid)
- company_id: string
- document_type: string (ej. csf)
- status: queued | processing | done | failed
- document_id: string|null
- error: string|null
- created_at, started_at, completed_at: datetime ISO8601
- processing_time_ms: integer|null

### 3.2 DocumentRecord (normalizado para integracion)
- document_id: string
- company_id: string
- document_type: csf
- parser_source: regex | hybrid_groq
- processing_status: processed | needs_review | pending_qr | incomplete | duplicate
- source_filename: string|null
- uploaded_at: datetime|null
- csf_hash: string
- extracted:
  - rfc
  - razon_social
  - regimen
  - cp
  - curp
  - id_cif
  - qr_text
  - qr_valid
  - qr_online
- field_validation: arreglo completo de campos (si/no por campo)
- corrected_json: objeto normalizado tras correccion

### 3.3 FieldValidation (obligatorio completo)
Un elemento por cada campo esperado del contrato:
- field: string
- label: string
- value: string|null
- status: si | no
- is_ok: boolean
- reason: ok | faltante | formato_invalido | mensaje de correccion
- suggested_value: string|null
- confidence: float|null

## 4) Flujo y Maquina de Estados

### 4.1 Flujo de procesamiento
1. Cliente envia documento.
2. API responde queued rapido con job_id.
3. Worker procesa y cambia a processing.
4. Worker termina en done o failed.
5. Cliente consulta estado por job_id.

### 4.2 Flujo de calidad y salida
1. Documento en done.
2. Operacion revisa field_validation.
3. Correccion por evento (si aplica).
4. Documento pasa a approved_for_sync.
5. Se emite evento document.approved.
6. Se publica a CRM/ERP con idempotencia.

## 5) Endpoints del Contrato

## 5.1 Ingesta
### POST /v1/documents
Request multipart:
- file: binary
- document_type: csf
- company_id: string
- validate_online: boolean

Response 200:
{
  "job_id": "uuid",
  "status": "queued",
  "document_type": "csf",
  "company_id": "demo-company",
  "document_id": null,
  "result": null,
  "error": null
}

### GET /v1/documents/{job_id}
Response 200 (done):
{
  "job_id": "uuid",
  "status": "done",
  "document_type": "csf",
  "company_id": "demo-company",
  "document_id": "uuid",
  "result": {
    "normalized_fields": {"document_id": "uuid"},
    "validation_flags": {},
    "artifacts": {
      "pdf_download_path": "/csf/{document_id}/pdf",
      "processing_time_ms": 12345
    }
  },
  "error": null
}

## 5.2 Documento enriquecido
### GET /csf/{csf_id}?include_ai_corrections=true
Response 200 incluye:
- crm_autofill
- ai_field_corrections
- corrected_json
- field_validation (completo)

## 5.3 Contrato objetivo de aprobacion (nuevo)
### POST /v1/documents/{document_id}/approve
Body:
{
  "company_id": "demo-company",
  "approved_by": "user-id",
  "notes": "revision completada"
}

Response 200:
{
  "document_id": "uuid",
  "company_id": "demo-company",
  "status": "approved_for_sync",
  "approved_at": "2026-03-29T20:00:00Z"
}

## 5.4 Contrato objetivo de salida externa (nuevo)
### POST /v1/sync/outbound
Headers:
- Idempotency-Key: string obligatorio

Body:
{
  "contract_version": "v1.0",
  "event_type": "document.approved",
  "event_id": "uuid",
  "event_time": "2026-03-29T20:00:00Z",
  "company_id": "demo-company",
  "document": {
    "document_id": "uuid",
    "document_type": "csf",
    "csf_hash": "sha256",
    "normalized_payload": {
      "tax_id": "...",
      "legal_name": "...",
      "tax_regime": "...",
      "postal_code": "...",
      "curp": "...",
      "cif_id": "..."
    },
    "quality": {
      "required_fields_ok": true,
      "score": 0.95,
      "missing_fields": []
    }
  }
}

Response 202:
{
  "accepted": true,
  "event_id": "uuid",
  "delivery_status": "queued"
}

## 6) Reglas de Idempotencia
- Toda salida externa requiere Idempotency-Key.
- La combinacion company_id + Idempotency-Key debe ser unica por 24h.
- Reintento con misma clave devuelve misma respuesta semantica.
- event_id debe ser unico globalmente.

## 7) Errores Estandar
Formato:
{
  "error_code": "STRING",
  "message": "texto humano",
  "details": {},
  "retryable": true
}

Codigos minimos:
- INVALID_DOCUMENT
- UNSUPPORTED_DOCUMENT_TYPE
- TENANT_FORBIDDEN
- JOB_NOT_FOUND
- VALIDATION_FAILED
- NOT_APPROVED_FOR_SYNC
- IDEMPOTENCY_CONFLICT
- EXTERNAL_DELIVERY_FAILED

## 8) SLA/SLO Targets

## 8.1 Disponibilidad
- API lectura/escritura: 99.9% mensual.

## 8.2 Latencia
- POST /v1/documents (ack queued): p95 <= 1.5s, p99 <= 3s.
- GET /v1/documents/{job_id}: p95 <= 500ms.

## 8.3 Procesamiento
- Tiempo queued -> processing: p95 <= 10s.
- Tiempo processing -> done (PDF estandar): p95 <= 120s, p99 <= 300s.

## 8.4 Calidad de datos
- Completitud minima de campos requeridos para aprobar: >= 95%.
- Tasa de parse fallido (failed): <= 2% mensual.

## 8.5 Entrega externa
- approved_for_sync -> queued outbound: <= 5s p95.
- delivery success a endpoint cliente: >= 99% con reintentos.
- Reintentos: 1m, 5m, 15m, 60m, 6h, 24h.

## 8.6 Trazabilidad
- 100% de cambios relevantes con entrada en audit trail inmutable.
- Verificacion de cadena de auditoria debe mantenerse valida en todo momento.

## 9) Criterios de Aprobacion para Sync
Un documento puede pasar a approved_for_sync solo si:
- status job = done.
- no existe error de parse.
- campos requeridos en si: tax_id, legal_name, tax_regime, postal_code, cif_id.
- field_validation de requeridos = si.
- validaciones de formato requeridas aprobadas.

## 10) Seguridad y Firma
- Auth JWT por tenant.
- Scope por tenant obligatorio en todos los eventos.
- Para webhooks/salida: firma HMAC del payload y timestamp anti-replay.

## 11) Versionado de Contrato
- contract_version en payload (ej. v1.0).
- Cambios breaking solo con nueva version (v2.0).
- Cambios non-breaking permitidos en misma version (campos opcionales).

## 12) Definicion de Listo (para pasar a implementacion)
- Esquemas JSON definidos y validados.
- Endpoints nuevos de approve/outbound creados.
- Idempotencia persistida en BD.
- Reintentos y DLQ definidos.
- Dashboards con SLIs: latencia, error rate, backlog, delivery success.
- Runbooks para incidentes y degradacion controlada.
