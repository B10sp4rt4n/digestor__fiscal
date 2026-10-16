# Digestor Fiscal: evaluación de integración

> Diagnóstico histórico sobre main antes de los ajustes. El PR se amplió después
> para implementar snapshot/gate, conflictos idempotentes y estados de entrega.
> La especificación actual propuesta está en CONTRATO_OPERATIVO_V1.md y el plan de
> transición en INTEGRACION_ROLLOUT.md. Los resultados y defectos descritos abajo
> corresponden a la base auditada, no todos al código actualizado del PR.

Fecha: 16 de octubre de 2026. Base: `9e251d761a23dccc554c11ba0a22274fd4f8e269` de main.

## Dictamen

Existe un prototipo funcional de ingesta y salida, apto para pruebas aisladas con datos sintéticos. No está demostrada la preparación para una integración comercial productiva multiempresa. La revisión encontró un defecto de escalamiento de privilegios, aprobación insuficiente y estados de entrega que pueden representar simulación. No se inspeccionó configuración, base de datos ni tráfico productivo; no se usaron documentos fiscales reales.

## Evidencia y límites

- Se clonó main y se revisaron contrato, rutas, modelos, servicios, script y pruebas. No había PR abiertos según la consulta del conector.
- El repositorio contiene dos archivos previos con seis pruebas (hash de auditoría y reporte de campos). No contiene `.github/workflows`; la consulta de runs asociados al commit devolvió vacío, pero el conector filtra a ejecuciones de PR y no prueba ausencia de otros CI.
- Se añadieron pruebas sintéticas con FastAPI TestClient, JWT local, SQLite en memoria y compilación de JSONB a JSON solo en tests. No validan semántica/concurrencia/migraciones PostgreSQL.
- Se genera un PDF sintético en memoria y se recorre HTTP upload → job → ejecución manual del worker real → parser → persistencia → aprobación → registro outbound → entrega con HTTP simulado → consulta.
- La cola se captura y su tarea se ejecuta manualmente. No se inicia el lifespan productivo, ni se comprueba recuperación de proceso o scheduling real.
- Se bloquea requests de red en las pruebas; IA, geocodificación y SAT online quedan desactivados. El receptor del test devuelve 200 mediante un doble de prueba, NO un servidor externo.
- Antes del fix: 14 pruebas aprobadas y una fallida: un admin obtuvo HTTP 201 al crear superadmin cuando debía recibir 403. Incluye las seis pruebas originales.
- Después del fix: 18 pruebas aprobadas (6 existentes + 12 nuevas), con avisos de deprecación. `bash -n scripts/e2e_railway.sh` válido.
- Reproducción: entorno Python 3.12 limpio, `pip install -r requirements-test.txt`; `python -m pytest -q`. Las pruebas de caracterización que aceptan fallos conocidos NO son criterios de seguridad aprobados: deben cambiar junto con sus correcciones.

## Matriz de capacidades

| Capacidad | Implementación y prueba local | Lo que falta para ofrecerla |
|---|---|---|
| Carga PDF y consulta | Ruta, job persistido, parser y worker recorridos con PDF sintético | Corpus sintético representativo, límites de tamaño/carga, recuperación tras reinicios |
| Carga masiva | ZIP legacy implementado, no incluido en recorrido nuevo | Límites de descompresión, carga y prueba ZIP |
| Extracción y hash | Parser real ejecutado; deduplicación usa SHA256(company_id + ':' + SHA256(bytes)) | No describir csf_hash como hash directo del PDF; precisión medida y corpus OCR |
| Validación | Reporte de 21 campos y correcciones probado por suite previa | No equivale a autenticidad SAT; aprobación no consume ese reporte |
| Aprobación | Presencia de cinco campos; actor tomado del JWT | Job done, formatos, errores, field_validation y snapshot aprobado |
| Registro outbound | BD, consulta tenant y clave explícita | Conflictos de contenido, carreras, normalización autoritativa |
| Entrega | Worker, HMAC, retry y DLQ implementados; éxito HTTP simulado y estados probados | Receptor sandbox real con acuse, anti-replay, exclusión entre workers y evidencia NOOP |
| Autorizaciones | JWT y guards; pruebas de 401/403, lectura job y evento entre tenants | Fix de escalamiento revisado/desplegado, configuración segura y revisión completa de rutas |
| Auditoría | Cadena parcial y eventos upload/approve/outbound | Atomicidad, concurrencia, identidad protegida y custodia externa |

## Hallazgos prioritarios

### P0 — admin puede crear superadmin

`app/api/routers/auth_router.py:register` limitaba tenant destino pero no el rol asignado. Un admin podía crear superadmin en su tenant y luego ese rol podía cruzar tenants por `can_access_tenant` y `enforce_tenant_scope`. Reproducido localmente con HTTP 201. Este PR bloquea asignación de superadmin salvo por otro superadmin (403), conservando altas operator/admin y altas autorizadas por superadmin. No se afirma explotación o presencia de usuarios afectados en producción. Revisar usuarios y logs mediante un proceso autorizado antes de habilitar clientes.

### P1 — aprobación no certifica calidad

`documents_v1.approve_document_for_sync` acepta RFC inválido, CP inválido y field_validation negativo si las cinco cadenas no están vacías. No exige job done ni ausencia de error de parse. Prueba local reproduce aprobación de documento needs_review sin job. No persiste approved_at como atributo dedicado, y repetir aprobación cambia fecha/auditoría. `approved_by` del ejemplo contractual no es un campo del request: el actor proviene correctamente del JWT. Aprobar no publica por sí mismo outbound; requiere segunda llamada.

Corrección posterior: validar los cinco campos con validadores comunes y reporte actual, vincular job o definir explícitamente el caso legacy, persistir revisión/snapshot/actor/fecha y aprobar transaccionalmente con auditoría. No activar un gate incompatible sin política para documentos legacy y correcciones IA.

### P1 — delivered puede no significar envío

`outbound_delivery_service._deliver_event` con URL vacía y `SYNC_OUTBOUND_ALLOW_NOOP_TARGET=True` (default) marca delivered sin red. Reproducido. El preview solo interpreta payload_json; no es acuse. El PR corrige descripción Swagger y script, sin alterar estados existentes.

Antes de piloto externo: NOOP=false, receptor sandbox configurado y evidencia de recepción ligada a event_id. Evolución: estado simulated o evidencia adicional explícita, conservando compatibilidad con consumidores existentes.

### P1 — idempotencia incompleta

`sync_v1` genera clave aleatoria por solicitud omitida. Dos solicitudes equivalentes sin cabecera crean eventos diferentes, comprobado. Coherente como facilidad de uso manual, incompatible con promesa de reintento seguro. Decisión propuesta: documentar la API opcional y requerir clave estable en el protocolo del integrador; no convertir silenciosamente V1 en cabecera obligatoria ni generar clave determinista solo por documento (impediría nuevas revisiones legítimas).

`sync_outbound_service` devuelve registro existente sin comparar cuerpo; dos documentos con la misma clave devuelven el primer evento, comprobado. Restricción BD es permanente mientras exista la fila, no de 24h. La consulta previa seguida de insert no resuelve carreras; IntegrityError de clave o event_id puede terminar en 500. El body persistido puede conservar company_id=null aunque la fila tenga tenant resuelto.

Corrección posterior: fingerprint de intención normalizada (tenant resuelto, documento/revisión, tipo de evento y contenido; tratar explícitamente event_id/event_time autogenerados), 409 ante contenido distinto, manejo de unicidad con rollback/relectura, pruebas concurrentes PostgreSQL y política de retención. Mantener clave explícita estable a través de errores de red. No prometer exactly-once.

### P1 — payload no representa necesariamente el documento aprobado

El cliente puede proporcionar csf_hash, normalized_payload y quality arbitrarios; solo se rellenan si son None. El script antiguo enviaba demo-hash y quality inventada. Defaults reales: event_type=csf_sync y claves rfc/razon_social/regimen/cp/id_cif; el contrato muestra document.approved y tax_id/legal_name/tax_regime/postal_code/cif_id. HTTP real es 200, no 202. event_type/document_type y score no tienen restricciones semánticas suficientes.

Corrección posterior: snapshot canónico del servidor con mapping/versionado, rechazo o separación de overrides externos. Pruebas de consistencia entre documento aprobado, hash y payload. Conservar nombres actuales en V1; introducir el esquema contractual estable mediante versión negociada.

### P1 — entrega y durabilidad

El worker selecciona eventos queued/retry sin claim, bloqueo ni lease: varios procesos pueden enviar el mismo evento. Un crash después de HTTP y antes de commit también permite duplicados. El destino y API_KEY son globales, no por cliente. No se envía Idempotency-Key al receptor; X-Event-Id puede usarse para deduplicar si el receptor lo implementa.

HMAC firma payload, pero no hay protocolo de timestamp de envío/ventana anti-replay. event_time del cuerpo no lo sustituye. MAX_ATTEMPTS=6 implica intento inicial más cinco reintentos; la demora de 24h de la lista nunca se programa con ese límite. failed_dlq es estado en tabla, no cola ni procedimiento de redrive independiente. La cola de ingesta es asyncio en memoria; jobs persistidos pueden quedar huérfanos tras reinicio. Archivo local usa segundo+nombre sin tenant, permitiendo colisiones de nombre en disco entre cargas.

Corrección posterior: outbox durable y claims con lease, receptor idempotente, destino/secretos por tenant, firma versionada con timestamp, reintentos definidos como intentos totales, redrive auditado, límites de carga y recuperación. Probar timeout/HTTP5xx/crash/concurrencia antes de SLO.

### P1 — trazabilidad no es inmutabilidad

`audit_service` protege action/entity/details, pero excluye user_id, company_id, status, timestamps y otros metadatos. Cambiar user_id no invalida verify_audit_chain, reproducido. No hay bloqueo ni unicidad de secuencia por tenant; dos writers pueden bifurcar cadena. Aprobación, encolado y entrega hacen commit antes de escribir audit log, por lo que un fallo deja cambios sin evidencia. No hay protección append-only ni ancla externa demostrada. El endpoint GET CSF puede persistir correcciones calculadas incluso para viewer sin evento específico de corrección. No se encontró circuito completo de corrección por evento con aprobación de cada cambio.

Corrección posterior: auditoría transaccional, secuencia serializada, hash versionado que incluya identidad y tiempo, permisos de almacenamiento y ancla externa. No reescribir hashes históricos; añadir versión nueva. Revisar correcciones y reingestas: `_persist_csf` puede actualizar campos y sustituir estado aprobado sin versionado de snapshot.

## Condiciones de seguridad operativa

JWT está activado por defecto, pero existen conectores none/header y secretos/password seed de ejemplo. Se desconoce configuración desplegada. Header sin proxy confiable no constituye identidad autenticada; none desactiva aislamiento. JWT usa claims sin consultar usuario activo en cada request: revocación/cambio de rol no invalida tokens ya emitidos inmediatamente. Sandbox público está habilitado por default y su rol es configurable. Verificar configuración sin imprimir secretos.

Validación online QR usa solicitudes HTTP con redirects y no exige previamente host SAT válido; necesita restricción de destino y egress antes de recibir entradas no confiables. Respuesta HTTP del SAT no prueba correspondencia fiscal del documento. No se ejecutaron pruebas de red ni consultas SAT.

El sync legacy tiene deduplicación global evt_hash y no comprueba pertenencia tenant de csf_id/sucursal_id/usuario_id. No se ensayó explotación; requiere revisión separada antes de declararlo multitenant seguro.

## Cambios incluidos y salida a comercial

Este PR mantiene formato HTTP, defaults, claves opcionales y estados actuales; agrega evidencia reproducible, corrige documentación y bloquea escalamiento no autorizado. El script requiere API_URL/PDF_PATH explícitos y ya no declara entrega externa validada. No se ejecutó dicho script ni se desplegó o fusionó nada.

Puede ofrecerse una evaluación técnica/piloto aislado con información sintética y revisión humana. No ofrecer todavía entrega garantizada, validación fiscal certificada, auditoría inmutable, operación multiempresa segura o SLA 99.9% como capacidad demostrada.

Secuencia de aceptación: (1) corregir y revisar autorizaciones/configuración; (2) snapshot y gate de calidad; (3) conflictos idempotentes y pruebas PostgreSQL concurrentes; (4) receptor sandbox con NOOP desactivado, acuse y anti-replay; (5) recuperación de jobs y auditoría atómica; (6) carga/latencia/errores medidos. La reunión fiscal del 19 de octubre no demuestra que estos alcances hayan sido acordados con Fidel.
