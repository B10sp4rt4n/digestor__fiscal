# Contrato operativo V1: ingesta, aprobación y salida

Este documento describe los cambios propuestos en el PR de endurecimiento del
16 de octubre de 2026. No demuestra que estén desplegados. La revisión histórica
sobre main está en [AUDITORIA_INTEGRACION_2026-10-16.md](AUDITORIA_INTEGRACION_2026-10-16.md).
La transición se detalla en [INTEGRACION_ROLLOUT.md](INTEGRACION_ROLLOUT.md).

## 1. Alcance y autenticación

JWT por tenant; roles operator/admin/superadmin pueden cargar, aprobar y registrar
salidas. Viewer puede consultar CSF y estado outbound, pero no aprobar ni cargar.
La consulta de jobs requiere operator/admin/superadmin. Superadmin tiene acceso
entre tenants. Solo superadmin puede crear otro superadmin. El actor de aprobación
se obtiene del JWT, nunca de approved_by en el cuerpo.

La configuración de producción debe usar JWT, sin acceso anónimo ni credenciales
predeterminadas. Los modos none/header son alternativas de configuración, no una
garantía equivalente. No se certifica seguridad productiva mediante este contrato.

## 2. Carga y consulta

- `POST /v1/documents`: multipart file PDF, document_type=csf, company_id opcional
  (resuelto desde JWT), validate_online=false por defecto. Responde HTTP 200 con
  job_id, status=queued, document_type y company_id.
- `GET /v1/documents/{job_id}`: queued/processing/done/failed; en done devuelve
  document_id y referencia al PDF. No devuelve un payload empresarial completo.
- `GET /csf/{csf_id}`: datos y validación persistidos; opciones de enriquecimiento
  pueden ejecutar IA/geocodificación según configuración.
- ZIP está disponible en `/upload/zip` legacy, no en `/v1/documents`.
- csf_hash es SHA256(company_id + ':' + SHA256(bytes)), no el hash directo del PDF.

La cola de procesamiento sigue en memoria. El job se persiste, pero su recuperación
tras caída de proceso no está garantizada. La validación online no certifica
identidad ni autenticidad fiscal ante SAT.

## 3. Aprobación verificable

`POST /v1/documents/{document_id}/approve`, body `{}` o `{"notes":"revisado"}`.
company_id en este body se conserva por compatibilidad pero el alcance se obtiene
del documento y se verifica contra JWT.

Requisitos:

1. Último job vinculado al documento y tenant en done y sin error.
2. processing_status en processed, pending_qr, duplicate o approved_for_sync.
   needs_review/incomplete no se aprueban. QR no es un requisito fiscal adicional
   de este gate: pending_qr permite revisión de los cinco campos requeridos.
3. RFC, razón social, régimen, CP e idCIF presentes y válidos con validadores
   locales compartidos; se rechazan placeholders DESCONOCIDO/SIN NOMBRE.
4. Para cada requerido, field_validation persistido debe tener status=si,
   is_ok=true y value coincidente con el valor actual. Reportes incompletos,
   negativos o desactualizados se rechazan. No se invoca IA durante aprobación.

Respuesta HTTP 200: document_id, company_id, status=approved_for_sync,
approved_at, approved_by, approval_id. Se persiste un snapshot del contenido
aprobado. Aprobar otra vez sin cambios conserva actor, fecha e identificador.
Una nueva aprobación de contenido diferente recibe otro approval_id.
Aprobación y su evento de auditoría se confirman en la misma transacción.

Documentos legacy sin job deben reingresarse por V1; no se fabrica evidencia de
procesamiento. Las correcciones IA pendientes pueden impedir aprobación; no existe
aún un flujo completo de resolución humana de todas esas correcciones.

## 4. Registro outbound

`POST /v1/sync/outbound`, HTTP 200 (se conserva por compatibilidad).

Cabecera `Idempotency-Key`: opcional en HTTP; necesaria para reintentar con
seguridad. Si se omite, se genera clave aleatoria y se devuelve en la respuesta.
Si se pierde la primera respuesta sin haber enviado clave, no hay garantía de
deduplicación. Clave explícita: 1 a 255 caracteres, no solo espacios.

Request mínimo:

```json
{"document":{"document_id":"UUID_APROBADO"}}
```

Defaults: contract_version=v1.0, event_type=csf_sync, event_id y event_time
se generan si se omiten. El ejemplo document.approved es una alternativa, no un
evento emitido automáticamente al aprobar. La salida requiere una llamada separada.

El servidor resuelve company_id y utiliza el snapshot aprobado. El cliente puede
ecoar csf_hash, normalized_payload, quality, document_type y approval_id, pero no
sustituirlos por otros valores. Datos distintos producen HTTP 409
APPROVED_PAYLOAD_MISMATCH. Si la fila cambió desde la aprobación, se devuelve
APPROVAL_STALE. Una aprobación antigua sin snapshot requiere nueva aprobación.

Payload persistido y enviado:

```json
{
  "contract_version":"v1.0",
  "event_type":"csf_sync",
  "event_id":"evt-...",
  "event_time":"2026-10-16T17:00:00Z",
  "company_id":"tenant-resuelto",
  "document":{
    "document_id":"uuid",
    "document_type":"csf",
    "approval_id":"uuid",
    "csf_hash":"hash-tenant",
    "normalized_payload":{
      "rfc":"...", "razon_social":"...", "regimen":"...",
      "cp":"...", "curp":null, "id_cif":"..."
    },
    "quality":{"required_fields_ok":true,"score":1.0,"missing_fields":[]}
  }
}
```

score=1 representa que los cinco campos pasaron este gate local, no una probabilidad
de autenticidad fiscal. Se mantienen nombres V1; tax_id/legal_name/postal_code son
nombres del reporte CRM, no los nombres outbound actuales.

Respuesta: accepted, event_id, delivery_status, delivery_mode e idempotency_key.
La creación del evento y su auditoría comparten transacción.

## 5. Idempotencia

- company_id + clave es único mientras exista la fila, sin TTL de 24 horas.
- Misma clave y misma intención devuelve el evento original; su estado puede avanzar.
- Documento, tipo/versión de evento, contenido explícito o metadatos explícitos
  distintos producen 409 IDEMPOTENCY_CONFLICT. No se sobrescribe el evento previo.
- event_id/event_time omitidos se refieren al evento original; los autogenerados
  de un nuevo request no causan un falso conflicto.
- Campos documentales omitidos se refieren al snapshot original, incluso después
  de reingesta o nueva aprobación. Para emitir una revisión nueva, usar otra clave.
- event_id tiene unicidad global; colisión devuelve 409 sin revelar otro tenant.
- Colisiones de inserción hacen rollback y relectura del ganador. Concurrencia de inserción probada en PostgreSQL 16 con dos sesiones; no
  equivale a probar múltiples workers de entrega.
- La idempotencia del registro NO garantiza exactly-once en el receptor.

## 6. Entrega y evidencia

`GET /v1/sync/outbound/{event_id}` respeta tenant y devuelve estado, intentos,
fechas, error y payload_preview. El preview es contenido persistido, no acuse.

| Estado/modo | Significado |
|---|---|
| queued | Registrado, pendiente de intento |
| delivered + http | Endpoint respondió 2xx; no demuestra procesamiento empresarial |
| simulated + noop | Simulación explícita; no hubo red y delivered_at es null |
| retry | Pendiente de reintento tras fallo |
| failed_dlq | Fallo terminal en tabla; no existe cola externa de redrive |
| blocked_review | Evento antiguo/malformado sin approval_id; requiere revisión |
| modo null | Sin intento nuevo o evidencia legacy desconocida |

NOOP está desactivado por defecto. Sin URL, el evento pasa a retry/failed_dlq;
no se marca delivered. NOOP=true sin URL produce simulated. No se reinterpretan
entregas históricas sin evidencia del modo usado.

El worker transmite X-Signature HMAC del payload, X-Event-Id e Idempotency-Key.
No sigue redirects; solo 2xx cuenta como entrega HTTP. Sigue usando URL y secreto
globales. Timestamp anti-replay, destinos por tenant, claims/leases y exclusión de
workers son pendientes. El receptor debe deduplicar por tenant+event_id.

Backoff definido: 1m, 5m, 15m, 60m, 6h, 24h. MAX_ATTEMPTS=6 implica intento inicial
más cinco reintentos: no se alcanza la demora de 24h con ese límite.

## 7. Errores y límites de auditoría

Los nuevos errores de dominio usan detail={error_code,message,details,retryable}.
Persisten respuestas FastAPI de texto/validación en otras rutas; no se promete un
sobre universal todavía. VALIDATION_FAILED y NOT_APPROVED_FOR_SYNC usan 422;
conflictos de aprobación/contenido/idempotencia usan 409.

La cadena de auditoría sigue protegiendo contenido parcial. No es almacenamiento
inmutable ni protege todos los metadatos. La entrega y su auditoría aún no son
atómicas; concurrencia de la cadena y custodia externa están pendientes.

## 8. Criterio comercial

Pruebas sintéticas locales y CI PostgreSQL 16 respaldan un piloto aislado.
Se validaron migraciones, inserciones simultáneas y recuperación entre sesiones.
No se validaron recuperación de proceso, workers concurrentes de entrega, receptor
externo de cliente ni SLAs productivos. 99.9% de disponibilidad, p95 de procesamiento y éxito de entrega
son objetivos por medir, no compromisos demostrados.
