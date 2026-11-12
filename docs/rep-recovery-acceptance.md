# Aceptación controlada de recuperación REP

El fixture `scripts/run_rep_recovery_fixture.py` utiliza FastAPI, JWT y las rutas
reales de pagos con PostgreSQL desechable. Sólo admite el host loopback y la base
`digestor_fiscal_test`; crea un esquema único y lo retira al terminar. Fija el
destino de la aplicación a SQLite antes de importar configuración y vacía los
tokens del proveedor. Las llamadas al PAC son funciones sintéticas y la red
externa de requests queda bloqueada. Este servidor nunca se importa en app.main.

Cotiza ejecuta el recorrido mediante `scripts/check_rep_recovery.mjs` y su prueba
`rep-recovery.integration.test.ts`, con otra base PostgreSQL desechable. El ensayo
usa la autorización y las rutas reales; únicamente simula la sesión humana.

Se comprueba pérdida de respuesta del registro del pago, emisión concurrente con
una reserva, recuperación rechazada mientras hay un trabajador activo, pérdida
del HTTP después de persistir el REP, primera lectura perdida, pérdida de la
respuesta del proveedor antes de persistir, rechazo de UUID ajeno, dos
recuperaciones simultáneas con un único acto, relectura idempotente y descarga
del mismo XML y PDF. Dos pagos de 58 mantienen parcialidades 1/2, pago 116,
saldo 0, base e IVA originales. Cada REP usa una sola llamada de emisión.

`test_rep_replacement_survives_lost_issue_cancel_and_status_without_changing_cash`
consolida la regresión sintética de la entrega histórica PR18: interrupciones
independientes en emisión de sustituta, cancelación y primera consulta. Conserva
el XML y cobro de origen; sólo activa la sustituta tras confirmar cancelación.
No ejecuta los scripts que emiten documentos en el PAC público de pruebas.

La aceptación local conectada pasó con PostgreSQL 18.6. Las 53 pruebas dirigidas
de pagos, recuperación y sustituciones pasaron sin omisiones en PostgreSQL.
La evidencia de CI y su commit se registran en el PR. Estas pruebas no acreditan
sesión Clerk productiva, SOAP productivo, sellos criptográficos ni la resolución
de los dos incidentes históricos del proveedor QA. El REP productivo queda
pendiente hasta que exista una factura PPD y un pago efectivamente recibido.
