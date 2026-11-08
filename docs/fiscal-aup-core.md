# Primer núcleo fiscal determinista y memoria de actos

Esta entrega aplica una combinación acotada: reglas ordinarias para aritmética y validación; transiciones explícitas de estilo AUP para emitir; memoria local con contrato Recordia para los actos observados. No agrega IA, llamadas externas de memoria ni custodia HotVault.

## Alcance y límites

La preparación para timbrar se limita a ingreso nacional MXN, PUE, formas 01/02/03/04/28, partidas objeto 02 e IVA 16%. Exige claves de producto y unidad explícitas (o del producto de catálogo seleccionado). No inventa emisor ni clasificación. Los RFC genéricos, moneda extranjera, PPD y otros perfiles tributarios quedan no listos y el servidor rechaza construir o timbrar su XML. La validación de códigos y datos fiscales es sintáctica; no certifica existencia/vigencia de catálogos SAT, compatibilidad régimen/uso, identidad o situación tributaria del receptor. El PAC conserva su validación.

Las unidades usan hasta seis decimales y las cuentas Decimal con HALF_UP a centavos. Se conserva la precisión en XML y representaciones. El almacenamiento heredado sigue siendo Float: se acota cada unidad a 8 enteros y 6 decimales y los totales a 12 enteros/2 decimales para preservar la ida y vuelta. Una migración futura a Numeric queda pendiente. No se recalculan documentos previamente timbrados.

PPD/REP y saldos, USD/tipo de cambio, cancelación/sustitución, notas de crédito, anticipos, parcialidades y perfiles exentos/tasa cero siguen pendientes. Esta entrega bloquea esos huecos; no los implementa.

## Transacciones y evidencia

Cada alta/edición guarda huella de datos y estados anterior/posterior. La reserva de timbrado se toma atómicamente. Antes de contactar al PAC se persisten la reserva y la huella SHA-256 del XML exacto de solicitud. El resultado y su acto se confirman juntos; el éxito conserva el XML original del PAC y su huella. Una respuesta incierta deja la reserva en unknown. Si falla la escritura del resultado después de llamar al PAC, permanece processing y se exige conciliación: no se repite automáticamente.

La tabla billing_fiscal_acts vincula empresa y borrador con FK compuesta, secuencia única, identificadores y hash encadenado. Cada extensión verifica la cadena y continuidad de estado. Correcciones agregan actos, sin reemplazar previos. Las escrituras comparten transacción con el estado; no se reutiliza audit_service porque realiza commit propio. Los rechazos previos a la reserva no generan actos. Borradores anteriores comienzan su historia en el primer acto observado; no se fabrica historia retrospectiva.

La identidad del actor es declarada por el contexto autenticado. Los hashes verifican integridad local; no son firmas ni evidencia de custodia externa. Un administrador de base puede desactivar triggers: falta anclaje externo para resistencia a ese actor. No hay endpoint nuevo para exportar esta bitácora ni integración externa Recordia/HotVault.

## Despliegue pendiente de aprobación

1. Mantener bloqueadas las escrituras y el timbrado; verificar respaldo y recuperación productivos y exclusividad de escritores según el plan previo.
2. Desplegar en mantenimiento (el lifespan no hace DDL, seed ni inicia workers).
3. Revisar y aplicar Alembic 20261025_01 después de 20260405_05. Agrega índice compuesto y tabla sin borrar datos. En PostgreSQL instala trigger que prohíbe UPDATE, DELETE y TRUNCATE. No ejecutar downgrade destructivo: la recuperación debe preservar historia.
4. Verificar tabla, FK, secuencia y trigger reales con el rol operativo. El arranque fuera de mantenimiento exige tabla existente, pero no certifica por sí solo todos los permisos/triggers.
5. Coordinar con el cliente Cotiza de perfil fiscal explícito: contratos anteriores sin claves ya no estarán listos. Los borradores anteriores conservan lectura; si sus totales o perfil no cumplen, requieren revisión y regeneración. Los timbrados mantienen su XML histórico.
6. Validar de extremo a extremo en entorno aislado antes de habilitar producción. No se habilitó producción ni se invocó un PAC real durante este trabajo.

## Verificación

La suite offline prueba precisión XML, perfiles bloqueados, totales alterados, transacciones del diario, alteraciones detectadas, rechazo/edición/reintento, fallos de auditoría antes y después del PAC y mantenimiento. Las pruebas PostgreSQL requieren exclusivamente la base descartable digestor_fiscal_test en loopback: concurrencia de dos conexiones, bloqueo durante llamada al proveedor y migración rerunnable con rechazo de UPDATE/DELETE/TRUNCATE y FK entre tenants. Sin esa URL se omiten explícitamente; CI fiscal-safety proporciona PostgreSQL 17 y PAC simulado.
