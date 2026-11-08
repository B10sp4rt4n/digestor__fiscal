# PPD y REP: publicación gradual

Perfil admitido: CFDI de ingreso nacional, MXN, IVA 16%, PPD y forma99. Los pagos son declaraciones del usuario; no hay consulta bancaria. El complemento conserva la parcialidad, saldo anterior, importe y saldo insoluto; el último pago absorbe el ajuste fiscal de centavos calculado por el motor.

Producción exige `FISCAL_PPD_ENABLED=true`, `FISCAL_REP_STAMP_ENABLED=true`, `FISCAL_PPD_PRODUCTION_ENABLED=true` y `FISCAL_PPD_PRODUCTION_EMITTER_RFC` con el emisor autorizado. Se conserva el token existente, SOAP HTTPS1443 y ruta exacta. No usar RFC, XML, UUID ni credenciales reales en este repositorio público.

El SAT debe responder Vigente antes de registrar un pago o timbrar un REP. Sin respuesta se detiene la escritura. La reserva durable anterior al PAC impide repetir un timbrado incierto. Su recuperación usa ObtieneCFDI y valida el XML contra la solicitud conservada, sin emitir de nuevo. El registro de pagos no acredita movimientos de banco ni valida criptográficamente los sellos.

## Instalación

Predeploy: `python scripts/install_fiscal_cycle.py`. Aplica sólo las cuatro migraciones fiscales aditivas, con transacción y bloqueo en PostgreSQL; no cambia Alembic legacy, documentos ni pagos existentes. Conserva y comprueba las tablas del piloto de cancelación real. Probar primero el paquete y conservar el despliegue anterior.

Mantener `FISCAL_CANCELLATION_ENABLED=false` y `FISCAL_CORRECTIONS_ENABLED=false`: las cancelaciones y sustituciones generales de este paquete siguen limitadas al sandbox. El piloto productivo existente conserva sus propios parámetros y evidencia.

Habilitar Cotiza sólo después de verificar el backend publicado: `DIGESTOR_FISCAL_PPD_ENABLED=true`, `DIGESTOR_FISCAL_PPD_PRODUCTION_ENABLED=true` para el tenant ya autorizado. Se exige sesión Clerk, origen coincidente y confirmación explícita al emitir REP real.

## Validación y límites

Pruebas sin PAC real: pago parcial58 + REP + pago final58 + REP + saldo0; SAT cancelado/no encontrado/timeout sin escritura; emisor y transporte ajenos; bloqueo de repetición; consentimiento y doble clic en React; instalación repetible en SQLite y PostgreSQL y evidencia inmutable. Se conserva el flujo de cancelación productiva anterior. Las pruebas usan comprobantes sintéticos; no son operaciones fiscales productivas ni pagos reales.

Para verificar posteriormente con operación real se requieren una nueva factura PPD y pagos efectivamente recibidos, sus fechas y referencias. No crear cobros ficticios. La consulta de saldo muestra pagos declarados, no conciliación bancaria.

Reversión: deshabilitar las capacidades y volver al despliegue anterior conservando tablas, XML y actos. Los comprobantes que ya se hubieran timbrado conservan efectos fiscales; una reversión de código no los cancela.
