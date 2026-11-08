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

Estas pruebas sintéticas no son certificación normativa ni aprobación fiscal. La prueba real posterior en QA público se documenta abajo; el ciclo de cancelación no ha sido aceptado por el proveedor.

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

El acceso Railway disponible sólo devolvió production y nombres de variables (valores ocultos). No se confirmó disponibilidad de una credencial/CSD sandbox privado ni se usó una credencial genérica como si fuera de pruebas. La prueba posterior usó únicamente el acceso y emisor públicos documentados por el proveedor. Las correcciones de importes/RFC, reasignación de cobros y casos fuera del perfil permanecen bloqueados.

## Ensayo real con QA público, 7 de noviembre de 2026 (CDMX)

Se encontró el token público de pruebas en la documentación oficial del PAC; no fue necesario solicitar credenciales privadas ni registrar CSD de SynAppsSys. El proveedor autoselló los XML usando su emisor de ejemplo. Se usó una base SQLite local separada, sin iniciar la aplicación productiva y con solicitudes restringidas a HTTPS del host QA, sin redirecciones ni reintentos automáticos.

- La primera solicitud se rechazó explícitamente con CFDI40139 por el nombre del emisor. El ejemplo oficial utiliza **INDISTRIA**; no se normalizó a INDUSTRIA. La siguiente corrida corrigió ese dato, con nueva evidencia.
- Factura PPD aceptada y validada con XSD firmado. Sustituta de factura aceptada con relación 04; no se activó porque no pudo confirmarse la cancelación del origen.
- En otra corrida independiente: factura PPD → abono 58 → REP1 aceptado → liquidación 58 → REP2 aceptado → cobrado 116 / saldo 0. Ambos XML se recuperaron mediante ObtieneCFDI y coincidieron con el contenido fiscal exacto de sus solicitudes, sin reemisión.
- CancelaCFDIAck devolvió HTTP500/código330230, EstatusUUID205, «Cfdi no existe en el Sat.» tanto para el origen de factura como para REP2. ConsultaEstatusSat respondió HTTP200, UUID correcto y Vigente. Se conservaron la cancelación sin confirmar, el origen, la sustituta pendiente y los cobros. No se equiparó el HTTP200 de consulta a una cancelación.

Resumen verificable, UUID y hashes de los archivos de evidencia: `docs/public-pac-pilot-20261107.json`. El paquete de evidencia conserva solicitudes/respuestas exactas y bases aisladas; no contiene headers de autenticación ni llaves privadas. No se verificaron criptográficamente los sellos y este ensayo no certifica la matriz normativa completa.

Reproducción opt-in (genera nuevos documentos QA; nunca reutilizar una carpeta existente):

```bash
.venv/bin/python scripts/run_public_pac_pilot.py --execute-public-sandbox --scenario payments --evidence-dir /tmp/pac-qa-nueva-corrida
```

El escenario `substitutions` intenta primero la sustitución de factura y se detiene si el origen continúa vigente. No habilitar producción por este resultado. La siguiente condición de liberación es aclarar o resolver el rechazo de cancelación con el PAC y acreditar la confirmación/activación completa en QA, además de la revisión fiscal y la sesión real de usuario.

Fuentes del contrato de prueba:
- https://developers.timbracfdi33.mx/docs/primeros-pasos
- https://developers.timbracfdi33.mx/docs/pase-a-producci%C3%B3n
- https://developers.timbracfdi33.mx/docs/acerca-de
- https://developers.timbracfdi33.mx/reference/timbrado_timbracfdi
