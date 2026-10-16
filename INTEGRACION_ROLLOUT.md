# Transición de aprobación y outbound — PR, no desplegado

## Cambios intencionales de comportamiento

- Admin ya no puede crear superadmin.
- Aprobar exige job V1 done sin error, campos/formato y reporte coincidente; se
  persiste snapshot con approval_id, actor y fecha. Repetición idéntica es estable.
- Outbound conserva HTTP 200, defaults de tipo y nombres de campos. La clave sigue
  opcional, pero se devuelve para reutilización; el integrador debe generarla antes
  del primer request si necesita recuperarse de una respuesta perdida.
- Clave reutilizada con otra intención devuelve 409. Overrides distintos del
  snapshot se rechazan. Una nueva revisión debe usar nueva clave.
- NOOP=false por defecto; modo de simulación explícito produce simulated.
- Eventos pendientes antiguos sin approval_id quedan blocked_review al intentarse.
  No se envían sus payloads controlados por el caller. No se recodifican delivered
  históricos, cuyo delivery_mode queda null/desconocido.
- No se siguen redirects HTTP hacia otros destinos.

Esto requiere avisar a consumidores que enumerren estados o enviaran payloads
arbitrarios. No activar en producción como actualización transparente.

## Despliegue propuesto (no ejecutado)

1. Respaldar BD y pausar procesamiento/outbound en el entorno a actualizar.
2. Revisar versión actual Alembic y probar `alembic upgrade head` sobre una copia
   aislada PostgreSQL. Nueva revisión: 20261016_01, padre 20260405_05.
3. Aplicar migración antes de iniciar código nuevo. Agrega cuatro columnas nullable
   a csf y delivery_mode nullable a document_sync_events. No inventa aprobaciones
   históricas ni acuses; no modifica el contenido de eventos existentes.
4. Revisar configuración JWT/secretos y URL destino sin imprimir secretos.
   Usar SYNC_OUTBOUND_ALLOW_NOOP_TARGET=false. Primero un receptor de pruebas,
   y solo información sintética. Evitar workers simultáneos mientras no haya leases.
5. Reprocesar documentos legacy a través de /v1/documents y revisar errores de
   validación. No editar estados en BD para saltarse el gate. Si las correcciones
   IA quedan pendientes, resolver el flujo de revisión antes de operar esos casos.
6. Revisar blocked_review: reingresar/reaprobar y crear una operación nueva con
   otra clave después de decidir si procede. Nunca reenviar masivamente sin revisión.
7. Con receptor externo sandbox, comprobar firma, acuse asociado a event_id,
   reintento tras timeout y una sola aplicación empresarial. El receptor debe
   deduplicar por tenant+event_id; no basta aceptar HTTP.
8. Medir fallos, backlog, recuperación y concurrencia antes de comprometer SLA.

## Pruebas incluidas y pendientes

`pip install -r requirements-test.txt` y `python -m pytest -q`.

Resultado local: **48 pruebas aprobadas** (incluye seis originales), con avisos de
deprecación. Alembic identifica una sola cabeza: 20261016_01.

Pruebas sintéticas: PDF real generado en memoria, parser y worker reales con cola
capturada; JWT/roles/tenant; aprobación y snapshot; rollback ante fallo de auditoría;
reintentos/conflictos/claves por tenant; HTTP2xx/timeout/redirect; simulación y legacy;
upgrade/downgrade de la migración nueva sobre esquema SQLite aislado.

Un receptor HTTP loopback real verifica firma y deduplica dos entregas por event_id.
No equivale a un receptor externo del cliente. La carrera de unicidad se inyecta
controladamente; no demuestra comportamiento concurrente en PostgreSQL. La migración
SQLite no sustituye ensayo de toda la cadena Alembic sobre PostgreSQL.

Pendientes: concurrencia PostgreSQL, leases, cola durable, destinos/secretos por
cliente, timestamp anti-replay, auditoría completa/serializada y resolución humana
de correcciones. El historial de auditoría conserva el diagnóstico previo a cambios.

## Reversión

Pausar workers antes de revertir. Preferir rollback de código manteniendo columnas
aditivas hasta revisar datos. El código anterior no reconoce simulated/blocked_review
y puede volver a marcar NOOP delivered; revisar explícitamente estos estados y
configuración. No ejecutar downgrade destructivo de columnas con evidencia de
aprobación sin respaldo y decisión de retención. No se ejecutó despliegue ni rollback.
