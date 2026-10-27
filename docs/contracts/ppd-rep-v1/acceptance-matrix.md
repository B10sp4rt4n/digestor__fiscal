# Matriz de aceptación para implementación

Casos que pasan hoy en el modelo están en test_reference.py (37) y test_wire.py
(13); no confundirlos con las pruebas de integración de esta matriz.

| Caso a implementar en entorno descartable | Preparación / acción | Resultado exigido |
|---|---|---|
| Ingreso PPD inicial | Mismo fixture fiscal PUE, perfil nuevo y PPD99; PAC mock confirma | UUID I estable, cuenta11600; no REP ni cobro implícito |
| Ingreso incierto | Timeout mock al emitir ingreso | No abre cuenta; reserva sigue; GET no genera timbre |
| Dos abonos | 4640 y6960 contra11600, confirmar REP en orden | Parcialidades1/2, saldos11600→6960→0, base4000/6000 e IVA640/960 |
| Cobro total diferido | 11600 tras fecha ingreso | Parcialidad1, saldo0; ingreso original sin cambio |
| Dinero aplicado sin REP | Confirmar cobro11600, timeout REP | Cobranza0, fiscal11600, pendiente REP11600; no cobro duplicado |
| Dos conexiones aplican | Recibo11600, ambos aplican8000 con misma revision | Sólo uno commit; otro409; suma≤11600, sin acto huérfano |
| Dos conexiones reservan | Intentos distintos mismo ingreso/parcialidad | Un intento activo; sin dos solicitudes PAC; unique y lock reales |
| Fallo de diario | Inyectar fallo insert acto antes de commit de aplicación | Saldo/aplicación/outbox rollback juntos |
| Falla antes de PAC | Builder mock falla después de reserva | Dinero conserva; evento libera reserva sólo con prueba de no envío |
| Falla tras PAC | Mock confirma, inyectar fallo commit | Reserva processing; nuevo submit409; reconciliar éxito una vez |
| Respuesta retrasada | EscritorA sin lease; escritorB concilia | Fencing impide aA persistir/reemitir; no duplicar parcialidad |
| Desconocido no emitido | Mock not_found eventual, luego aparece XML | No liberar por primer not_found; identidad y documento únicos |
| No emisión concluyente | Evidencia terminal válida, permisos y fencing | Rechazo registrado; nuevo intento sólo autorizado, conserva anterior |
| Idempotencia cruzada | Mismo key mismo body; mismo key importe distinto; mismo key otro tenant | Replay estable;409 conflicto; aislamiento entre tenants |
| Doble captura bancaria | Misma cuenta interna/ID movimiento con distintos keys | Segunda captura rechazada; no segundo saldo |
| Duplicado sospechoso manual | Monto/fecha/receptor iguales, IDs distintos o ausentes | Revisión y evidencia; dos pagos legítimos pueden resolverse sin fusionarlos |
| Aplicación varias facturas | Mismo receptor/emisor, total pago232, dos aplicaciones116 | Un REP, dos DR, secuencias independientes y suma exacta |
| Receptor/emisor distinto | Mezclar documentos de dos clientes/emisores |422; cero mutaciones o exposición cross-tenant |
| Sobrante | Pago12000, deuda11600 |400 no aplicado; no saldo negativo ni anticipo automático; v1 no prepara REP hasta resolver |
| Corrección previa a reserva | Desaplicar100 mediante evento corrects y aplicar a otra factura compatible | Compensación auditada, conserva evento inicial, revisión aumenta |
| Corrección posterior | Modificar aplicación en processing/unknown/stamped |409 y expediente de corrección fiscal; no editar documento |
| REP externo duplicado | Mismo ingreso/parcialidad ya documentado por otro sistema | Suspender emisión, importar/conciliar evidencia mediante flujo autorizado |
| Banco revierte pago | REP stamped y contracargo posterior | Abre disputa; no cancela UUID ni reemite automáticamente |
| Cancelación externa | Detectar ingreso/REP cancelado en conciliación autorizada | Quarantine de cadena afectada, reconstrucción sin borrado; no reutilizar parcialidades |
| Microimportes/residuos | Base0.01; muchas líneas pequeñas;100 pagos; último centavo | DR/P dentro de tolerancias vigentes, sumas por concepto reproducibles; bloquear casos insolubles |
| Inyección fiscal | Cliente envía parcialidad, saldo, impuesto o company_id |422 contrato cerrado; Digestor deriva de ledger y binding autenticado |
| Fecha real atrasada | Captura noviembre de pago octubre | Deadline5nov, overdue cuando corresponda; no cambiar FechaPago |
| Fecha fuera de secuencia | Nuevo pago con fecha anterior a REP ya confirmado | Revisión y bloqueo de emisión; nunca renumerar documentos existentes |
| Outbox duplicado/desorden | Reenviar intent; repetir evento; entregar secuencia3 antes2 | Un efecto; inbox unique; gap bloquea proyección hasta recuperar2 |
| Documento falso o ajeno | UUID válido pero RFC/DR/Monto/hash/TFD no coincide | Quarantine; no aplicar fiscalmente ni liberar reserva |
| Memoria | Rol app intenta UPDATE/DELETE/TRUNCATE, FK tenant incorrecta, alteración hash | DB rechaza rewrite/enlace; verificador detecta alteración; no afirmar custodia externa |
| Regresión PUE | Requests y XML fixtures de PR35/Digestor3 antes/después | Mismos resultados/contratos/XML; PPD sigue bloqueado mientras gates apagados |
| Recuperación | Backup/restauración sintética con intent unknown e historia | Reserva, UUID, secuencias, outbox/inbox y hashes conservados tras restaurar |

Cada prueba de infraestructura debe usar PostgreSQL17 descartable de loopback,
fixtures ficticias y mock PAC que cuente llamadas. Fallar si intenta URL de base
remota o proveedor real. No ejecutar migraciones de estas pruebas en producción.
