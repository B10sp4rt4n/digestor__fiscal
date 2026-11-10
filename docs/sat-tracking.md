# Seguimiento SAT de documentos conocidos

GET `/v1/billing/drafts/{draft_id}/sat-status?expected_uuid=...` recupera evidencia conservada sin llamadas externas. POST en la misma ruta consulta el servicio SAT y agrega una observación al registro de auditoría existente. Para REP se usa `/payments/{payment_id}/receipt/sat-status` bajo el mismo draft. No requiere migración ni credenciales adicionales.

El UUID debe corresponder al XML original o al comprobante seleccionado por el historial de revisiones del mismo tenant y pago. El entorno debe ser productivo y el emisor debe coincidir con `FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC`. La consulta no cambia dinero, XML, timbrado ni estados de cancelación. Un resultado discrepante requiere conciliación posterior.

Cada observación conserva fecha UTC, fuente, hash del XML consultado y campos SAT permitidos. Las fallas se registran con mensaje genérico, sin excepciones ni respuestas crudas. El último intento y la última respuesta contestada son independientes; se devuelven hasta 20 entradas recientes y se conserva la última respuesta aun tras más de 20 fallas. Los escritores del audit trail se serializan por tenant en PostgreSQL para evitar bifurcaciones concurrentes.

La consulta reutiliza el adaptador SOAP directo al SAT. La opción de aceptar N 602 se usa únicamente en seguimiento; los gates existentes de emisión/cancelación siguen rechazando respuestas no confirmadas. N 601 y fallas de transporte no se clasifican como documento no localizado. Fuente técnica: https://www.sat.gob.mx/minisitio/Factura/documentos/cancelacion/ar_consulta_cfdi.pdf (consultada 10 noviembre 2026).

Se recuperan también las respuestas de status_consulted en las actas de cancelación y del piloto histórico, con las fechas conservadas; no se usa la hora de lectura como fecha SAT. Se reutiliza el resultado reciente durante 15 segundos para evitar repeticiones. No hay actualización periódica en esta entrega.
