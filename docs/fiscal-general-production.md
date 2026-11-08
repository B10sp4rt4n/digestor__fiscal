# Casos fiscales sin esperar operaciones comerciales

Separar el avance del producto de la evidencia de una transacción real. Los escenarios controlados permiten desarrollar y verificar cada transición. Una factura timbrada sintética o una respuesta SAT simulada nunca se presenta como operación productiva comprobada.

## Incremento actual

Cancelación de factura o REP con motivos02/03; motivo01 a través de una sustitución emitida y verificada. El transporte productivo usa CancelaCFDIAck40 y consulta directa SAT, con destino HTTPS exacto, JWT obligatorio, acceso anónimo deshabilitado, emisor configurado y administración para mutaciones. El tenant y documento se resuelven en servidor.

La sustitución conserva RFC, importes y cobros; permite corregir los datos fiscales y método/forma de pago del perfil existente. El original sigue activo hasta confirmar su cancelación. Recuperar una emisión incierta consulta el XML por UUID y valida la solicitud conservada; no la emite otra vez.

| Caso controlado | Criterio |
| --- | --- |
| Acuse201 y aceptación pendiente | Estado solicitado; no afirmar cancelación |
| SAT En proceso | Mantener pendiente |
| SAT Solicitud rechazada | Mostrar rechazo; no reenviar automáticamente |
| Respuesta del PAC perdida | Reservar y conservar resultado incierto; un solo envío |
| SAT sin respuesta o No cancelable | Detener antes de reservar/enviar |
| Doble clic/concurrencia PostgreSQL | Una sola petición al PAC |
| Sustitución y motivo01 | Verificar sustituta vigente; adoptar UUID nuevo sólo tras confirmación |
| Recuperación de sustituta | Validar contenido exacto y UUID; no retimbrar |
| REP con parcialidades posteriores | Resolver en orden inverso; conservar cobros y saldo económico |
| Historial del piloto anterior | Conservar XML, acuse y estado aunque se retire su habilitador |
| Acceso ajeno | Bloquear tenant/emisor/rol/origen no autorizado |

Cotiza muestra consecuencias y confirmaciones de producción. Los controles nuevos se muestran a administración con sesión Clerk. Las confirmaciones se validan en servidor. El PDF original mantiene su condición fiscal, incluidas sustituciones históricas. El panel del piloto no se duplica al habilitar el flujo general.

## Publicación

Aplicar sobre el esquema fiscal ya instalado por `python scripts/install_fiscal_cycle.py`; este incremento no introduce migraciones nuevas. Conservar las tablas de evidencia, XML y parámetros existentes del piloto.

Backend: `FISCAL_CANCELLATION_ENABLED=true`, `FISCAL_CORRECTIONS_ENABLED=true`, `FISCAL_CANCELLATION_PRODUCTION_ENABLED=true`, `FISCAL_CORRECTIONS_PRODUCTION_ENABLED=true` y `FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC` con el emisor autorizado. Conservar PPD/REP y credenciales existentes. Publicar primero el código correcto del backend y verificar salud.

Cotiza: `DIGESTOR_FISCAL_CANCELLATION_ENABLED=true`, `DIGESTOR_FISCAL_CORRECTIONS_ENABLED=true`, `DIGESTOR_FISCAL_OPERATIONS_PRODUCTION_ENABLED=true`. Publicar después de verificar backend; comprobar lecturas y paneles sin emitir/cancelar documentos durante la instalación.

Reversión: apagar los habilitadores y volver al despliegue anterior conservando las tablas y evidencia. El código anterior mantiene las nuevas rutas apagadas. Una reversión de software no revierte un comprobante ya emitido o una cancelación fiscal.

## Límites y siguientes incrementos

No incluye cambios de RFC, cantidades/precios, otros perfiles de impuestos, USD, notas de crédito, reasignación o devolución bancaria de pagos, cancelación automática de factura con cobros, motivo04 ni importación de cancelaciones realizadas fuera de Cotiza. Los pendientes del piloto anterior conservan su ruta hasta resolverlos; una cancelación confirmada permanece visible aunque se retire su habilitador.

Siguiente orden de desarrollo: USD/tipo de cambio, notas de crédito y perfiles de impuestos. Crear pruebas controladas para cada caso antes de su publicación. La prueba comercial PPD con pagos efectivamente recibidos queda pendiente independiente y no bloquea estos incrementos.
