# Piloto de cancelación productiva

Entrega independiente sobre la versión productiva: no requiere publicar la cadena PPD/REP en desarrollo. Sólo admite una factura PUE cuyo ID, UUID y hash de XML se fijan por configuración; el valor de esos parámetros no se publica en el repositorio. No admite sustituciones ni facturas con cobros declarados o relación saliente.

La ruta requiere JWT y pertenencia al tenant del borrador. Cotiza exige además sesión Clerk y rol owner/admin/superadmin, deriva el borrador de la propuesta del tenant y comprueba Origin y confirmación explícita. No hay acceso bootstrap ni selección arbitraria de emisor/UUID/motivo. El motivo del piloto es03, por la declaración del usuario de que la operación fue de prueba.

Antes de enviar, el SAT debe identificar el comprobante vigente y cancelable sin aceptación. El intento se reserva de forma durable y única por tenant/UUID. La operación usa `CancelaCFDIAck40` del SOAP productivo ya configurado, con los nombres de parámetros verificados en su WSDL. No se extrae, imprime ni reemplaza la credencial existente. Se prohíben otros hosts, redirecciones y reenvíos, aun cuando se pierda la respuesta.

El XML original y el estado comercial permanecen. El acuse se vincula al emisor/UUID y se conserva como evidencia. Únicamente una consulta posterior al SAT con Estado Cancelado produce ese estado local. Las consultas y respuestas tienen una cadena de hashes y eventos protegidos contra UPDATE/DELETE. El PDF de Cotiza etiqueta cancelación pendiente o confirmada según el registro fiscal, sin alterar el XML.

## Preparación y reversión

1. Ejecutar en predeploy `python -m app.services.production_cancellation --install`. Sólo agrega dos tablas nuevas e instala sus guardas de evidencia; no altera documentos existentes. Es repetible y fue probado en SQLite y PostgreSQL aislado, con dos conexiones concurrentes y un único envío simulado al PAC.
2. Configurar `LIVE_CANCEL_ENABLED=true`, `LIVE_CANCEL_DRAFT_ID`, `LIVE_CANCEL_UUID`, `LIVE_CANCEL_XML_SHA256` en el servicio existente. Se requiere `TIMBRACFDI_ENV=production`, su token activo y URL SOAP productiva exacta. La aplicación exige el esquema de evidencia antes de arrancar con la capacidad activa.
3. En Cotiza, configurar `DIGESTOR_FISCAL_LIVE_CANCEL_UUID` para el mismo documento. El panel sólo se muestra a administradores autorizados en su propuesta. No se modifican las credenciales de integración.
4. Solicitar una sola vez y consultar. Una cancelación fiscal confirmada no se revierte con un rollback de software. Revertir al despliegue anterior y desactivar la capacidad detiene nuevas solicitudes, conservando las tablas y evidencia. Conservar los identificadores de los despliegues previos antes de publicar.

Comprobaciones: documento/XML fijados, aislamiento por tenant, sesión y rol, origen de la petición, motivo fijo, ausencia de reenvío, acuse ajeno, consulta vigente frente a cancelada, integridad y tablas repetibles. La prueba con dinero/CFDI real sólo se acredita después de obtener su respuesta productiva; los tests no la simulan como éxito externo.
