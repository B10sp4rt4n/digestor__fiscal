# Recuperar una factura sin solicitar otro timbre

El resultado `unknown` conserva el bloqueo de emisión. Un operador puede aportar
un UUID confirmado por el proveedor y ejecutar
`POST /v1/billing/drafts/{draft_id}/stamp-result/recover` con
`{"invoice_uuid":"UUID","confirm":true}`. El endpoint conserva autenticación,
roles y tenant de la sesión. Sólo consulta `ObtieneCFDI`; no llama a timbrado.

Para solicitudes nuevas, `request_prepared` conserva la huella del XML original,
la estructura comercial sin atributos de firma ni TimbreFiscalDigital, y la
huella del borrador. La recuperación exige cadena local íntegra, UUID idéntico y
coincidencia de toda esa estructura. Guarda XML, transición y métrica en una
transacción bajo bloqueo del borrador; repetir la recuperación es idempotente.

Se bloquean `processing`, UUID ajeno, cambios en receptor/importes/partidas/
impuestos/método, XML corrupto, fallo del proveedor e historial antiguo sin
solicitud verificable. `processing` tras caída de un worker requiere revisión
operativa; no se libera por tiempo transcurrido. La comparación es conservadora:
normalizaciones léxicas benignas del proveedor pueden exigir revisión manual.
Las huellas no verifican criptográficamente los sellos ni el estado del SAT y
esta evidencia local no acredita custodia externa en HotVault.

No requiere migraciones ni servicios adicionales. Publicar primero Digestor y
después el consumidor Cotiza. Antes de habilitar el recorrido productivo,
verificar con el PAC sandbox su respuesta real de `ObtieneCFDI`, incluyendo
normalizaciones del XML; las pruebas aisladas no sustituyen esta validación.

## Comprobación reproducible

`tests/test_invoice_recovery.py` verifica recuperación, HTTP/autorización,
integridad, rollback y concurrencia PostgreSQL mediante
`BILLING_TEST_DATABASE_URL` en una base loopback desechable llamada
`digestor_fiscal_test`. La suite corriente omite los casos de PostgreSQL si no
se proporciona esa variable. El workflow fiscal ejecuta PostgreSQL 17.

`scripts/run_invoice_recovery_fixture.py PUERTO` inicia un FastAPI de prueba en
127.0.0.1 con SQLite desechable y un proveedor sintético. Bloquea todas las
peticiones `requests` externas; no importa el arranque de producción. Se usa
desde `scripts/check_invoice_recovery.mjs` de Cotiza para verificar HTTP/SQL,
respuesta perdida y descargas XML/PDF. Nunca desplegar este fixture como servicio.

Los borradores sin folio aportado usan su identificador estable como folio
automático; dos creaciones dentro del mismo segundo conservan identidades
separadas. Un UUID ya asociado a otro borrador se rechaza, incluso si ambos XML
son comercialmente idénticos. En PostgreSQL, un bloqueo transaccional por
tenant/UUID serializa recuperaciones concurrentes antes de asociar el documento.
Los folios aportados explícitamente conservan su valor.
