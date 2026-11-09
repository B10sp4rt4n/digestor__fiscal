# Cierre del recorrido PPD → pagos → REP → saldo

Entrega del 9 de noviembre de 2026. Perfil nacional MXN/IVA16, dos pagos de
58.00 sobre factura de116.00. Sin bancos ni datos de clientes.

## Cambios

- `economic_settled` indica saldo declarado en cero; `receipts_pending_count`
  cuenta complementos por resolver y `fiscal_complete` exige además factura y
  complementos listos en el expediente de aplicación. No acredita conciliación
  bancaria ni una consulta nueva de vigencia ante SAT.
- `processing` no permite recuperar mientras el trabajador original puede seguir
  timbrando. Consultar estado es lectura; si un trabajador cae y conserva ese
  estado, requiere revisión manual. La recuperación externa sólo admite `unknown`.
- Antes de consultar el proveedor y antes de guardar, se comprueban historial,
  declaración, solicitud original congelada y factura. XML de solicitud alterado,
  historial corrupto o histórico sin evidencia verificable conserva el bloqueo.
- Cotiza conserva bloqueo visual de emisión si se pierde la respuesta, evita
  segunda confirmación por doble clic y distingue saldo liquidado de REP pendientes.
  Descargar XML exige el mismo UUID que representa el PDF.

No requiere migración, variable nueva, servicio adicional ni cambio de capacidad
productiva. Publicar primero Digestor y después el consumidor Cotiza.

## Evidencia

Prueba conectada de Cotiza: rutas reales, Prisma/PostgreSQL desechable, HTTP real,
FastAPI/SQLAlchemy/SQLite y PAC sintético. Cubre respuesta de registro perdida,
reintento con la misma clave, referencia duplicada, conflicto de idempotencia,
concurrencia de REP, orden de parcialidades, REP2 desconocido, UUID ajeno,
recuperación sin reemisión, descargas XML/PDF, liquidación y sobrepago bloqueado.

Pruebas PostgreSQL del backend: trabajador de timbrado activo frente a solicitud
de recuperación, y dos recuperaciones concurrentes de `unknown` que sólo agregan
un acto final. Casos SQLite verifican alteración de XML/historial, falta de evidencia
y diferencia entre liquidación económica y completitud fiscal.

El PAC público REST sandbox aprobó diez comprobaciones: factura original XSD y
aceptación, REP1 emitido/recuperado, saldo cero con REP2 desconocido, recuperación
idempotente de REP2 y completitud final. Exactamente tres `TimbraCFDI` (una factura,
dos REP) y dos `ObtieneCFDI`; todos HTTP200. No se solicitaron cancelaciones.
Identidad y token públicos de demostración del proveedor. Solicitudes/respuestas
sintéticas y reporte: `docs/evidence/ppd-recovery-20261109/`.

Reproducir sólo en un directorio nuevo y con consentimiento de ensayo QA:

```
.venv/bin/python scripts/run_public_pac_pilot.py --execute-public-sandbox --scenario payments-recovery --evidence-dir RUTA_NUEVA
```

La aceptación REST sandbox y los XSD no prueban criptográficamente sellos,
movimientos bancarios, todos los escenarios fiscales ni el transporte SOAP de
producción. No se registraron pagos ni se emitieron comprobantes productivos.
