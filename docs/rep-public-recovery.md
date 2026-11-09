# REP: aceptación en PAC y respuesta perdida antes de persistir en Digestor

`scripts/run_public_rep_recovery.py --execute-public-sandbox --evidence-dir RUTA_NUEVA`
usa exclusivamente la identidad pública de demostración del PAC sandbox,
una base SQLite local desechable y las rutas reales de pagos mediante ASGI TestClient.
No inicia el servicio publicado ni utiliza credenciales o datos de clientes.
La identidad local del operador está simulada; no es un ensayo JWT publicado,
PostgreSQL concurrente ni del transporte SOAP productivo.

Emite una factura PPD/99 de 116 MXN, declara un pago único por el total y emite
un solo REP. Después de que el adaptador real recibe la aceptación del PAC,
descarta deliberadamente esa respuesta antes de que Digestor guarde el resultado.
La reserva y la solicitud quedan conservadas y el complemento pasa a `unknown`.
Un reintento de emisión se rechaza con 409 sin otra llamada al PAC.
`ObtieneCFDI` recupera el UUID confirmado por el operador y el XML íntegro.
Recuperaciones repetidas no vuelven a consultar ni emitir; un UUID ajeno se rechaza.
Se conserva un solo pago, saldo cero e historial registrado→reservado→preparado→
incierto→recuperado.

El destino, método, ruta y número de llamadas se restringen a tres peticiones:
`TimbraCFDI` de factura, `TimbraCFDI` de REP y `ObtieneCFDI`. Se bloquean producción,
redirecciones y llamadas adicionales antes de transmitir. No ejecutar en servicios.

## Resultado del 9 de noviembre de 2026

Factura QA: `e23ad3e5-8676-489e-9167-596903f394ec`.
REP: `8feeec54-8e9e-4e40-bda9-7e0c3c333723`.
Una aceptación de REP, una recuperación real del PAC, un pago de 116.00 MXN,
saldo 0.00, cadena de actos válida y conciliaciones repetidas sin otra llamada.

El primer cotejo del arnés exigía igualdad de bytes y falló después de una
recuperación HTTP 200. El PAC sólo reordenó una declaración `xmlns:tfd` dentro
del mismo elemento: ambos XML completos son idénticos tras canonicalización,
incluidos UUID, datos fiscales y sellos. Se conservan los dos XML originales,
sus huellas distintas y el reporte inicial. No se relajó la validación productiva.

La finalización se retomó sobre el mismo checkpoint local con `--resume-confirmed`:
ese modo exige el reporte esperado y un resultado ya recuperado, bloquea toda
petición adicional al PAC y verifica otra vez identidad íntegra, estado SQL,
saldo e idempotencia. No hubo una nueva emisión para corregir el arnés.
La canonicalización no constituye verificación criptográfica de sellos o SAT.

Las pruebas aisladas de pagos/recuperación aprobaron 44 casos; cinco escenarios
PostgreSQL se omitieron sin una base loopback de pruebas configurada.
