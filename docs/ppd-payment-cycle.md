# PPD y recepción de pagos: flujo de validación sandbox

## Resultado y límite de liberación

Sobre el núcleo fiscal anterior, esta entrega prepara una factura nacional MXN/IVA16 con método PPD y forma99, declara cobros parciales/totales, calcula saldos y prepara un CFDI P con complemento Pagos2.0. Incorpora reserva de emisión y manejo de resultados inciertos para probar timbrado con proveedor simulado o sandbox configurado. No habilita producción.

FISCAL_PPD_ENABLED=false y FISCAL_REP_STAMP_ENABLED=false son los valores predeterminados. Incluso con flags activos, el servidor exige TIMBRACFDI_ENV=sandbox y destino HTTPS de pruebas reconocido tanto para el perfil PPD como para el flujo de pagos. PUE conserva su comportamiento. No se enviaron comprobantes ni se accedió a bancos durante el desarrollo.

## Contrato y alcance

- POST /v1/billing/drafts/{id}/payments recibe idempotency_key UUID, reference, amount decimal positivo con hasta2 decimales, paid_at con zona y payment_form01/02/03/04/28. No recibe tenant, saldos, tasas ni UUID de factura del cliente. Usa JWT/rol y la empresa del contexto.
- GET .../payments devuelve total original, suma declarada, saldo y recibos. Una factura PUE devuelve supported=false. El XML original timbrado, sus datos fiscales, UUID y huella son la base; no se reconstruyen desde una cotización editable.
- GET .../payments/{payment_id}/receipt consulta el estado/XML almacenado sin volver a llamar al PAC.
- POST .../receipt/prepare devuelve XML preliminar, sin persistir una emisión ni llamar al PAC.
- POST .../receipt/stamp requiere ambos flags y sandbox. Una respuesta emitida debe incluir UUID y conservar los datos de negocio del REP solicitado. Respuestas cambiadas/incompletas y timeouts dejan unknown. No hay reintentos automáticos.

El primer perfil es un pago aplicado a una factura MXN con todas las partidas objeto02/IVA16, sin retenciones, descuentos, relaciones, moneda extranjera ni anticipos. Se rechazan pagos futuros, previos a la factura, fuera de orden cronológico, superiores al saldo y parcialidades más allá de999. No hay conversión de moneda. La distribución acumulada conserva base/impuesto originales, base a seis decimales e IVA a centavos. Comprueba los límites oficiales del abono y del remanente liquidable; si no existe una asignación válida, se rechaza antes de registrar. Esto incluye ciertas facturas con diferencias de redondeo entre base e IVA: falta ampliar y validar estos casos con la matriz fiscal/PAC.

El XML propuesto incluye CFDI4.0 tipoP, monedaXXX, total/subtotal0, usoCP01, concepto84111506/ACT/Pago y ObjetoImp01. Pagos2.0 expresa total de pagos, impuestos MXN, fecha/forma/moneda del cobro, UUID original, parcialidad, saldo anterior, pagado e insoluto, y trasladosDR/P. La fecha original con offset se conserva; se normaliza UTC para orden y se representa en America/Mexico_City en el XML.

Referencias oficiales consultadas (diseño, no certificación):
- Estándar Pagos2.0 del SAT: https://www.sat.gob.mx/cs/Satellite?blobcol=urldata&blobkey=id&blobtable=MungoBlobs&blobwhere=1461175070885&ssbinary=true
- Esquema referenciado: http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos20.xsd

Se agregó validación offline con XSD CFDI/Pagos/Timbre y catálogos fijados, más reglas adicionales del perfil. No se cubre toda la matriz SAT ni se obtuvo aceptación real del PAC; ver docs/rep-schema-and-rounding.md. El XML preliminar no está sellado ni timbrado. Verificar CSD, firmado/ensobretado y contrato del PAC en sandbox antes de cualquier liberación. La emisión de la factura de ingreso ya usa el adaptador existente; este cambio no cambia el contrato de firmado de ese adaptador.

## Integridad y concurrencia

La fila de factura se bloquea en PostgreSQL al registrar; secuencia, clave y referencia se protegen con restricciones únicas. Idempotencia: misma clave/mismo contenido devuelve el pago original; cambiar contenido devuelve409. La referencia y la clave son únicas dentro de la empresa, también entre facturas. Cambiar deliberadamente referencia y clave puede evadir la detección: no equivale a conciliación bancaria automática. El registro queda como declaración del operador (recorded_claim), bank_verification=not_performed, y no afirma que un banco lo confirmó.

Los pagos son append-only y sus importes Numeric; el total de factura no se modifica. Cada declaración conserva instantánea y huella del XML de ingreso, su fecha original, datos y saldos. La bitácora de complemento registra estados/actor/huellas con contrato Recordia, en la misma transacción del estado. Sin conexión externa a Recordia ni custodia HotVault.

Recibo reservado y solicitud exacta se confirman antes del PAC, sin mantener bloqueos de base durante la red. Un fallo al guardar el resultado conserva processing con solicitud durable. Una reserva processing/unknown o evidencia previa bloquea la repetición. Rechazo explícito permite reenviar el mismo recibo sin sumar el pago otra vez. Un complemento posterior espera al timbrado del anterior. Consultar estado sólo lee lo persistido; la recuperación de un resultado externo desconocido requiere conciliación operativa futura.

## Migración y pendientes

20261025_02 sigue a20261025_01: agrega billing_payments, billing_payment_receipts y billing_payment_acts, FK compuestas por empresa y triggers PostgreSQL contra UPDATE/DELETE/TRUNCATE en declaraciones/actos. No modifica ni fabrica pagos históricos. Se impide downgrade destructivo. Administradores de base pueden desactivar triggers; los hashes locales no son firma ni custodia externa.

Revisar/aplicar en mantenimiento, con respaldo/recuperación productivos y exclusividad de escritores del plan anterior. Activar primero sólo en entorno aislado, coordinado con Cotiza (DIGESTOR_FISCAL_PPD_ENABLED=true). Esta entrega permanece en borrador.

Pendientes de liberación: matriz fiscal/identidad/vigencia y aceptación sandbox real; perfiles de redondeo agregado todavía no representables; conciliación externa y recuperación de unknown; correcciones/reversiones de cobros, cancelación/sustitución, PDF propio del REP, pagos agrupados a varias facturas, anticipos, retenciones y otras monedas. No activar productivo antes de cerrar estos puntos aplicables. No se contrataron servicios nuevos ni se usó IA de pago.

## Pruebas

Suite offline: payload estricto, JWT/roles/empresa, PPD99/PUE, saldos parciales y cierre exacto, duplicados, orden/fechas, XML REP y datos devueltos, timeout/rechazo/reintento, fallos de bitácora antes/después del proveedor y alteración de evidencia. CI con PostgreSQL17 descartable prueba además sobrepago concurrente, idempotencia simultánea, dos solicitudes al mismo recibo con una llamada PAC, migración repetible y triggers/FK. Todos los proveedores de prueba están simulados; no confundir la suite con aceptación fiscal real.
