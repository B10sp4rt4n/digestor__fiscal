# Cancelaciones y recuperación de REP: implementación de pruebas

Rama apilada sobre PR4 (`2ba5247`), coordinada con el incremento Cotiza sobre PR36. No habilita producción.

## Comportamiento

Cancelación es un agregado independiente de la emisión y de los cobros. `billing_cancellations` conserva identidad/hash del XML, intención/motivo/sustituta, clave de operación, resultado y acuse. `billing_cancellation_acts` preserva actos append-only con actor de sesión del servicio y hashes. Esto no acredita firma del SAT ni custodia externa; Cotiza usa su cuenta de servicio y el actor backend no identifica por sí solo al humano que pulsó el botón.

Las rutas resuelven factura/REP dentro del tenant JWT. Reservan la intención bajo bloqueo de factura y confirman la transacción antes de llamar al proveedor. Los reintentos con la misma clave y contenido consultan la solicitud existente. Timeout, respuesta desconocida o caída después del proveedor mantienen la reserva. Sólo `not_sent`, cuando el proveedor no fue contactado por falta de configuración, admite repetir la misma operación. Cambiar clave/motivo no salta una solicitud pendiente.

El acuse corresponde al UUID/emisor original; se guarda como evidencia de solicitud. Sólo `ConsultaEstatusSat` con éxito, UUID coincidente y `EstadoSat=Cancelado` confirma cancelación. Una consulta fallida no libera la reserva ni vuelve a enviar la cancelación. No se infiere cancelación por plazos, mensaje libre o código HTTP200.

Una factura con pagos queda bloqueada para cancelación. Un REP posterior emitido o incierto impide cancelar el anterior hasta resolverlo; los posteriores con cancelación confirmada permiten continuar en orden inverso. Cancelar REP no modifica importe/cobro, saldo económico ni XML original. Una cancelación confirmada/pendiente sin sustituta activa bloquea preparación/emisión fiscal posterior incluso al apagar el flag de cancelaciones.

Motivos02/03 admitidos. Motivo01 exige una sustituta local verificada del mismo tenant y comprobante, ya timbrada y con relación04 al UUID original. Puede resolver factura o REP desde FiscalRevision; no se acepta sólo un UUID declarado. Motivo04 y resolución completa de factura con pagos permanecen bloqueados.

Recuperación REP: POST receipt/reconcile recibe receipt_uuid, consulta `ObtieneCFDI` y exige coincidencia del UUID, estructura de negocio/XSD y solicitud durable. Actualiza evidencia/estado en una transacción y no emite otro REP. No descubre automáticamente UUIDs desconocidos ni borra cobros.

## API

- GET/POST `/v1/billing/drafts/{id}/cancellation`: consultar/solicitar.
- POST `.../cancellation/reconcile`: consultar estado al proveedor, sin reenviar cancelación.
- Mismas operaciones bajo `/payments/{payment_id}/receipt/cancellation` para REP.
- POST `/payments/{payment_id}/receipt/reconcile`: recuperar XML timbrado por UUID confirmado.

`FISCAL_CANCELLATION_ENABLED=false` por defecto; el transporte nuevo usa REST y valida su propio destino sandbox aunque el timbrado heredado use SOAP. `FISCAL_PPD_ENABLED` y `FISCAL_REP_STAMP_ENABLED` conservan sus guardas. La migración20261107_01 sigue a20261025_02 y debe revisarse/aplicarse en mantenimiento con respaldo y recuperación; no hubo cambios productivos. Al habilitar PPD/cancelaciones se comprueba el esquema requerido antes del arranque.

## Fuentes consultadas, 7 nov2026

- SAT proceso de cancelación, motivos, sustitución y consulta: https://www.sat.gob.mx/minisitio/Factura/cancela_procesocancelacion.htm
- SAT complemento de pagos2.0: https://wwwmat.sat.gob.mx/consultas/92764/comprobante-de-recepcion-de-pagos
- Contratos primarios del proveedor (OpenAPI incluido en la versión Markdown):
  - https://developers.timbracfdi33.mx/reference/timbrado_cancelacfdiack
  - https://developers.timbracfdi33.mx/reference/timbrado_consultaestatussat
  - https://developers.timbracfdi33.mx/reference/timbrado_obtienecfdi

No son certificación normativa, aprobación fiscal ni prueba del proveedor real. No se probaron CSD/firmado ni aceptación PAC en esta entrega. Sigue pendiente completar la sustitución/corrección fiscal y el piloto real aislado antes de liberar.

## Pruebas reproducibles

`DATABASE_URL=sqlite:///:memory: .venv/bin/python -m pytest -q tests` ejecuta offline. Para concurrencia, proporcionar BILLING_TEST_DATABASE_URL sólo a una base PostgreSQL descartable loopback llamada digestor_fiscal_test; el fixture rechaza cualquier otro destino y usa un esquema nuevo por prueba.

`run_fiscal_cycle_fixture.py` sirve exclusivamente en127.0.0.1, usa SQLite en memoria, JWT/datos sintéticos, reemplaza los cuatro métodos del PAC por simulaciones y prohíbe requests externos. Su segundo REP simula una respuesta perdida después de emitir y permite comprobar la recuperación por UUID desde Cotiza. No desplegar este fixture.

## Sustituciones fiscales con economía conservada

`FiscalRevision` conserva por separado XML fuente, solicitud exacta, declaración de corrección, estado/UUID/XML de resultado y actos append-only. La migración20261107_02 agrega las dos tablas e índice único de corrección activa por UUID de origen; descartar una corrección nunca enviada permite preparar otra sin eliminar evidencia. Las solicitudes paralelas se serializan con el bloqueo de la factura y restricciones de base. El estado incierto sólo se recupera por consulta y coincidencia exacta del XML/UUID.

El perfil implementado permite corregir metadatos de receptor y método/forma en facturas MXN sin pagos, o forma/referencia del último REP. Conserva RFC y economía: partidas, tasas, importes, fechas de pago y aplicaciones no se editan. La nueva emisión incluye relación04 al documento anterior; cancelar con motivo01 resuelve el UUID de sustitución exclusivamente desde la revisión emitida del mismo tenant/documento, nunca desde una dirección arbitraria del navegador.

La consulta de estado confirma la cancelación anterior antes de adoptar la sustituta como activa. El pago posterior usa el UUID activo sin sobrescribir la factura raíz. Para REP conserva la declaración económica y el XML anterior; la siguiente parcialidad queda bloqueada hasta completar la corrección. Se permiten cadenas de sustitución y se verifican sus hashes/actos en cada recorrido.

Rutas GET/POST `.../correction` de factura y `.../payments/{payment_id}/receipt/correction`; acciones POST separadas `stamp`, `reconcile` (UUID confirmado), `abandon`, `cancel-original`, `confirm-cancellation`. Las lecturas autenticadas de evidencia no requieren activar escrituras. Las mutaciones requieren `FISCAL_CORRECTIONS_ENABLED=true`, destinos de transporte sandbox comprobados y los flags PPD/REP/cancelación que correspondan; todos conservan sus valores seguros por defecto.

Arranque: PostgreSQL exige el esquema fiscal correspondiente. Se corrigió la creación automática heredada de tablas: create_all sólo corre en SQLite de desarrollo; en PostgreSQL las migraciones son explícitas. Aplicar las migraciones en mantenimiento con la cadena previa, respaldo y recuperación; esta entrega no las aplica a producción.

Validación: sustitución de factura, REP y cadena sucesiva; pago posterior vinculado a UUID activo; conservación de cobro/XML original; resultado incierto, recuperación y rechazo de cambios de negocio; descarte antes de emitir; aislamiento JWT y flags. PostgreSQL descartable comprueba preparación concurrente idempotente, una única llamada de emisión ante dos solicitudes, migración repetible, FK y triggers append-only. La contraparte Cotiza prueba el recorrido entre servicios por HTTP con emisor/JWT/datos/PAC sintéticos.

El acceso Railway disponible sólo devolvió production y nombres de variables (valores ocultos). No se confirmó disponibilidad de una credencial/CSD sandbox ni se usó una credencial genérica como si fuera de pruebas. No hubo firmado ni aceptación real del proveedor. Las correcciones de importes/RFC, reasignación de cobros y casos fuera del perfil permanecen bloqueados; se conserva esta diferencia frente a una liberación fiscal completa.
