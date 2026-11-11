# Numeración interna de facturas por empresa

Primera entrega: prefijo (opcional), año opcional, consecutivo inicial y mínimo de 1–12 dígitos. Reinicio anual sólo con año en la serie; reloj America/Mexico_City. El UUID fiscal no se configura.

GET/PUT /v1/billing/invoice-numbering usa únicamente la empresa del JWT y exige admin/superadmin. PUT estricto incluye expected_revision; no admite company_id. Configuración y evento con antes/después confirman juntos. authenticated_actor es la cuenta Digestor; declared_actor conserva la identidad de Cotiza como declaración, sin otorgarle permisos.

## Reserva

Guardar la primera configuración incorpora únicamente las prefacturas creadas posteriormente. Se identifican con numbering_managed; antes de timbrar muestran “Por asignar al timbrar”. Las pendientes administradas usan la política vigente en el momento de reservar. Las antiguas conservan su serie/folio.

Después de reclamar el timbrado y antes de confirmar stamp_reserved, se bloquea la numeración de la empresa, se asigna el siguiente número y se conserva una reserva con restricción única. La reserva, el consecutivo, el estado processing y el acto fiscal confirman juntos antes del PAC. No se sostiene el bloqueo durante la red. Rechazos, fallos locales y respuestas inciertas conservan la reserva; conciliación/reintento no incrementa el consecutivo. Nunca se devuelve un número cancelado.

El control compara también los folios numéricos históricos, independientemente de sus ceros iniciales. La revisión cambia al guardar o reservar: una edición administrativa desactualizada devuelve 409. Cambiar de prefijo y volver a una serie utilizada exige continuar por encima del último número; cambio anual genera otra serie. Prefijos vacíos y sin año omiten Serie en el XML. Los dígitos son un mínimo, sin truncamiento al crecer.

## Liberación

1. Respaldar y aplicar la migración 20261111_01_invoice_numbering en copia de la base; probar dos aplicaciones y comparar documentos previos. Requiere las migraciones fiscales anteriores y la restricción compuesta de billing_draft.
2. Aplicar esa migración en la base destino antes de desplegar el modelo nuevo. No usar create_all como migración productiva. La migración es aditiva y no renumera ni timbra documentos; el campo nuevo queda false para los previos.
3. Desplegar backend. FISCAL_NUMBERING_ENABLED es false por defecto; habilitarlo hace disponible la configuración, pero una empresa sigue con su método previo hasta que su owner guarda su política.
4. Publicar la interfaz Cotiza y verificar GET autenticado en cada conexión habilitada. Confirmar una política y un ciclo en el entorno de pruebas antes de un timbrado real autorizado.

Las reservas y cambios tienen triggers PostgreSQL que impiden UPDATE/DELETE/TRUNCATE. Una reversión operativa preserva modelos/columnas/tablas; no volver a un binario anterior que ignore numbering_managed. Desactivar el flag sólo detiene nuevas altas/configuraciones: las prefacturas administradas ya creadas siguen reservando con su política.

## Límites

No agrega emisores/conexiones por tenant a Cotiza: usa exclusivamente las conexiones fiscales existentes. No configura REP, notas de crédito ni sustituciones; éstas mantienen sus series existentes. El número inicial permite continuidad con otro sistema, pero no importa su historial: el owner debe conocer el último folio usado y evitar emitir la misma serie desde otro sistema sin coordinación.

Validación automatizada con datos sintéticos: permisos, aislamiento, revisión concurrente, límites, cambio anual, reserva/reintento, rechazo, timeout y legado. La prueba PostgreSQL usa sólo BILLING_TEST_DATABASE_URL loopback desechable y comprueba concurrencia e inmutabilidad. No solicita timbres reales.


## Verificación local — 11 de noviembre de 2026

97 pruebas fiscales aprobadas contra PostgreSQL 18.6 desechable, incluidas 23 de numeración. Comprobados: dos reservas concurrentes distintas, conflicto entre dos altas administrativas, migración dos veces sobre una fila previa, triggers de inmutabilidad, rollback conjunto de folio/estado/acto, aislamiento y rechazo de un XML cuyo folio/serie difiere de la solicitud. Sin red al PAC.

Suite amplia: 338 aprobadas, 54 subtests aprobados y 10 fallos de OCR de CSF; el caso de imagen sin rotación también falla en el código base 631244c en este entorno. La suite completa y el recorrido visual no están aprobados. Envío a GitHub y apertura de PR autorizados por el usuario el 11 de noviembre de 2026. La publicación del código no aplica migraciones ni habilita folios reales.

## Preparación de liberación

El instalador ya usado por Railway incluye la migración de numeración y convierte únicamente su conexión Neon a destino directo; la aplicación conserva su pool. Pruebas fiscales actualizadas: 99 aprobadas con PostgreSQL desechable, incluidas 25 de numeración/liberación. La migración de modelos y guards se ejecutó dos veces en una copia de producción, con digest de los documentos intacto y ninguna política o reserva creada. Respaldo anterior a la liberación conservado con caducidad. GitHub Actions sigue en cola; no se declara aprobado.

## Arranque aislado del instalador

La migración registra explícitamente el modelo de billing_draft, necesario cuando Railway ejecuta el instalador en un proceso nuevo sin haber importado la aplicación. La regresión reprodujo NoReferencedTableError antes del ajuste. Después, el CLI completo pasa dos ejecuciones en procesos nuevos sobre un esquema PostgreSQL previo, conservando documentos y sin crear reservas. Suite fiscal relevante: 100 pruebas aprobadas.
