# Integridad de sustitución y corrección

Entrega del 9 de noviembre de 2026, sobre el PR #16. Una emisión de sustituta
en estado `processing` conserva su reserva: `reconcile` sólo consulta al PAC
cuando el estado persistido es `unknown`. Esto evita que una recuperación
compita con el trabajador original que todavía puede estar timbrando.

El consumidor debe consultar primero el historial tras perder una respuesta;
no debe repetir emisión, cancelación o descarte desde una vista antigua.
Los trabajadores abandonados en `processing` conservan revisión manual.
No se libera una reserva por tiempo ni se transforma automáticamente en rechazo.

Pruebas reproducibles: `tests/test_fiscal_revisions.py` acredita rechazo de
recuperación durante el envío original y amplía el ensayo PostgreSQL de dos
conexiones. El resto del ciclo conserva relación 04, motivo 01, activación tras
confirmación de cancelación, historial original y pagos económicos.

Validación local sobre el código combinado con #16: 298 pruebas y 54 subpruebas
aprobadas; 17 omitidas por sus condiciones externas, entre ellas PostgreSQL.
Se usó el modelo OCR español existente; el primer intento sin ese modelo falló
en diez casos de OCR ajenos al cambio. La repetición configurada aprobó la suite.
GitHub ejecuta PostgreSQL17 desechable para la concurrencia omitida localmente.

El recorrido Cotiza–Digestor usa HTTP real, FastAPI/SQLAlchemy/SQLite en memoria
y proveedor sintético. No acredita aceptación PAC sandbox, confirmación SAT real
ni sellos criptográficos. Sin migraciones, flags, servicios nuevos, timbrados,
cancelaciones, pagos ni comunicaciones productivos.

Publicar después de #16 y antes del consumidor correspondiente de Cotiza.
