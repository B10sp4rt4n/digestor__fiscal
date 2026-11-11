# Seguimiento SAT de documentos conocidos

GET `/v1/billing/drafts/{draft_id}/sat-status?expected_uuid=...` recupera evidencia conservada sin llamadas externas. POST en la misma ruta consulta el servicio SAT y agrega una observación al registro de auditoría existente. Para REP se usa `/payments/{payment_id}/receipt/sat-status` bajo el mismo draft. No requiere migración ni credenciales adicionales.

El UUID debe corresponder al XML original o al comprobante seleccionado por el historial de revisiones del mismo tenant y pago. El entorno debe ser productivo y el emisor debe coincidir con `FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC`. La consulta no cambia dinero, XML, timbrado ni estados de cancelación. Un resultado discrepante requiere conciliación posterior.

Cada observación conserva fecha UTC, fuente, hash del XML consultado y campos SAT permitidos. Las fallas se registran con mensaje genérico, sin excepciones ni respuestas crudas. El último intento y la última respuesta contestada son independientes; se devuelven hasta 20 entradas recientes y se conserva la última respuesta aun tras más de 20 fallas. Los escritores del audit trail se serializan por tenant en PostgreSQL para evitar bifurcaciones concurrentes.

La consulta reutiliza el adaptador SOAP directo al SAT. La opción de aceptar N 602 se usa únicamente en seguimiento; los gates existentes de emisión/cancelación siguen rechazando respuestas no confirmadas. N 601 y fallas de transporte no se clasifican como documento no localizado. Fuente técnica: https://www.sat.gob.mx/minisitio/Factura/documentos/cancelacion/ar_consulta_cfdi.pdf (consultada 10 noviembre 2026).

Se recuperan también las respuestas de status_consulted en las actas de cancelación y del piloto histórico, con las fechas conservadas; no se usa la hora de lectura como fecha SAT. Se reutiliza el resultado reciente durante 15 segundos para evitar repeticiones.

## Seguimiento periódico optativo

El servicio existente ejecuta un trabajador de lecturas SAT; no necesita otro servicio, IA, credenciales PAC nuevas ni tareas programadas de ChatGPT. La funcionalidad está apagada por defecto y no inscribe documentos anteriores automáticamente.

1. Aplicar la migración aditiva `20261110_01` (o el instalador revisado `scripts/install_fiscal_cycle.py`, si la instalación conserva el historial Alembic legado). No hace backfill ni modifica facturas, pagos, REP, cancelaciones o XML. Conservar el esquema al revertir el código.
2. Configurar `SAT_TRACKING_AUTOPOLL=true` y `SAT_TRACKING_TENANTS` como lista JSON explícita de IDs autorizados. Requiere JWT sin anónimo, entorno productivo y el emisor ya configurado en `FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC`. No habilita otro emisor ni tenants nuevos por omisión. Publicar Digestor antes de Cotiza.
3. Un administrador de Cotiza activa cada UUID desde su expediente; puede pausarlo desde el mismo panel. El backend admite roles operator/admin/superadmin; Cotiza restringe esta gestión a owner/admin/superadmin y verifica origen, tenant y visibilidad de propuesta. La consulta manual sigue disponible con su alcance anterior.

POST `/v1/billing/drafts/{draft_id}/sat-status/schedule?expected_uuid=...` recibe exclusivamente `{"enabled":true}` o `{"enabled":false}`. Para REP, agregar `/payments/{payment_id}/receipt` antes de `/sat-status/schedule`. No consulta al SAT al inscribir ni pausar. Repetir el mismo estado es idempotente. El cambio conserva autor y evidencia en el audit trail.

GET/POST de consulta manual agregan `schedule` con disponibilidad, permiso de gestión, estado inscrito y ejecución efectiva, intervalo, próxima revisión UTC, fallos y último desenlace. Son compatibles con despliegues sin la tabla nueva: la consulta manual sigue funcionando y la programación aparece no disponible. Una configuración revocada pausa la ejecución aun si la inscripción sigue conservada; el administrador puede desinscribir el documento.

La primera revisión queda pendiente para el siguiente tick del trabajador. Después se revisa cada seis horas (`SAT_TRACKING_INTERVAL_SECONDS=21600`, rango 900–86400). Tick predeterminado: 60 segundos; lote máximo predeterminado: diez documentos, uno a la vez por proceso. Fallos: reintentos a 15, 30, 60 minutos y siguientes duplicaciones, hasta el intervalo normal. No se inventa un estado fiscal ante una falla y se conserva la última respuesta contestada. Las observaciones distinguen `trigger=manual` de `trigger=scheduled`.

Cada trabajo conserva tenant, draft, pago cuando corresponda, UUID y hash de XML. Se valida antes de la lectura y se usa la revalidación del servicio manual antes de registrar la observación. Un documento o evidencia incompatibles pausan el trabajo para revisión, sin modificar el estado fiscal. PostgreSQL usa `FOR UPDATE SKIP LOCKED` y un token de reserva persistido durante cinco minutos; no mantiene transacciones ni bloqueos de fila durante la petición externa. Otro trabajador no reclama una reserva vigente; un reinicio puede recuperar las vencidas. El token evita que un trabajador anterior reactive un documento pausado o complete una reserva reemplazada. El apagado espera la lectura en curso y no inicia otro documento.

Para detener el despliegue completo, desactivar `SAT_TRACKING_AUTOPOLL` o retirar el tenant de la lista y reiniciar el servicio de forma normal. No borrar la tabla ni el historial. No se sincronizan estados internos ni se concilian discrepancias automáticamente. La funcionalidad no descarga documentos desconocidos, no resuelve los incidentes PAC QA y no acredita REP productivo.

Validación reproducible: `tests/test_sat_scheduler.py` cubre inscripción, pausas, aislamiento, contrato HTTP, reintentos, reserva tras reinicio, cambios de XML, historial y REP sin alterar dinero. Incluye reclamación concurrente con dos conexiones PostgreSQL en la base desechable de CI; las pruebas locales sin esa base se marcan omitidas.
