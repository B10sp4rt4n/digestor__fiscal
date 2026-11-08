# Cancelaciones y recuperación de REP: implementación de pruebas

Rama apilada sobre PR4 (`2ba5247`), coordinada con el incremento Cotiza sobre PR36. No habilita producción.

## Comportamiento

Cancelación es un agregado independiente de la emisión y de los cobros. `billing_cancellations` conserva identidad/hash del XML, intención/motivo/sustituta, clave de operación, resultado y acuse. `billing_cancellation_acts` preserva actos append-only con actor de sesión del servicio y hashes. Esto no acredita firma del SAT ni custodia externa; Cotiza usa su cuenta de servicio y el actor backend no identifica por sí solo al humano que pulsó el botón.

Las rutas resuelven factura/REP dentro del tenant JWT. Reservan la intención bajo bloqueo de factura y confirman la transacción antes de llamar al proveedor. Los reintentos con la misma clave y contenido consultan la solicitud existente. Timeout, respuesta desconocida o caída después del proveedor mantienen la reserva. Sólo `not_sent`, cuando el proveedor no fue contactado por falta de configuración, admite repetir la misma operación. Cambiar clave/motivo no salta una solicitud pendiente.

El acuse corresponde al UUID/emisor original; se guarda como evidencia de solicitud. Sólo `ConsultaEstatusSat` con éxito, UUID coincidente y `EstadoSat=Cancelado` confirma cancelación. Una consulta fallida no libera la reserva ni vuelve a enviar la cancelación. No se infiere cancelación por plazos, mensaje libre o código HTTP200.

Una factura con pagos queda bloqueada para cancelación. Un REP posterior emitido o incierto impide cancelar el anterior hasta resolverlo; los posteriores con cancelación confirmada permiten continuar en orden inverso. Cancelar REP no modifica importe/cobro, saldo económico ni XML original. Una cancelación confirmada/pendiente bloquea preparación/emisión fiscal posterior incluso al apagar el flag de cancelaciones.

Motivos02/03 admitidos. Motivo01 de factura exige una sustituta local del mismo tenant, ya timbrada, mismo emisor/receptor y relación04 al UUID original; no se acepta sólo un UUID declarado. Motivo04, sustitución REP y resolución completa de factura con pagos permanecen bloqueados.

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
