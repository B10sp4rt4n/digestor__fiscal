# Preparación SAT → Digestor → CIMA · contrato 0.1.0

Estado: contrato e importador offline con SQLite/PostgreSQL desechables. No hay conector de descarga,
tablas productivas nuevas, rutas HTTP, sincronización real ni receptor CIMA activado. La preparación de los
pasos 7/8 no cierra su implementación. No requiere PPD ni servicios adicionales.

## Base revisada

Digestor main 4ee8bee349ca5d449d10ffcae7e7643e10c83a62 ya consulta estados de UUID
conocidos mediante sat_tracking; eso no descubre documentos emitidos/recibidos.
sync_outbound es un contrato csf_sync para CSF aprobadas: no se reutiliza su
document_type=csf para metadatos fiscales.

CIMA corresponde al repositorio B10sp4rt4n/fradma_dashboard3, revisado en
4c75dfe9c3c8c2b30fd6b952406e7a00ae9487b5: contracts/v1/cfdi_document.schema.json,
cfdi_legacy_mapping.yaml, cfdi/ingestion.py y CFDI_CANONICAL_BASELINE_V1.md.
El canónico exige moneda, subtotal, descuento y versión CFDI; no se pueden
completar con valores por defecto desde metadata. La ingesta legada verifica
UUID global y escribe ventas/clientes; no se usa para este nuevo dataset.

Fuente oficial revisada el 12 nov 2026:
[SAT: consulta y recuperación](https://wwwmat.sat.gob.mx/consultas/42968/consulta-y-recuperacion-de-comprobantes-(nuevo)).
Documenta metadata TXT delimitada por ~, identidad, fechas, total, efecto y estado;
el web service requiere e.firma. El adaptador de ese TXT y las operaciones SOAP
deben verificarse contra el formato/WSDL antes de implementarlas. Este contrato
interno no presume nombres de columnas ni códigos de estado del proveedor.

## Modelo lógico propuesto (sin migración aplicada)

| Registro | Identidad / contenido | Regla |
|---|---|---|
| Autorización empresa | tenant + empresa + RFC + capacidad | Se resuelve en servidor; nunca por el primer documento |
| Solicitud | tenant + empresa + RFC + dirección + periodo + tipo metadata | Intención idempotente; estado local separado del CFDI |
| Paquete/manifiesto | solicitud + paquete proveedor + hash + referencia inmutable | Conservar bytes originales y recuentos antes de interpretar |
| Documento | hash(tenant, empresa, RFC, UUID) | Un documento puede existir sin XML y sin propuesta Cotiza |
| Observación | identidad documento + hash de contenido y procedencia | Append-only; nuevo estado agrega evidencia |
| Entrega CIMA | dataset + observación + consumidor | Acuse idempotente; no confundir descarga con entrega |
| Vínculo Cotiza | documento + ID interno resuelto dentro del tenant/RFC | Encontrado / externo / ambiguo; ambiguo bloquea vínculo |

Los campos auth tenant, empresa y RFC se obtienen de membresía/configuración
fiscal. Los IDs de Cotiza y Digestor no se consideran idénticos: requieren
un mapeo explícito autorizado. raw_ref apunta a evidencia privada, no a URL
firmada temporal ni a ruta suministrada libremente por quien carga el archivo.

## Flujo de sincronización a implementar

1. Validar capacidad, membresía, RFC, dirección y periodo. Piloto máximo 31 días
   naturales, límites internos configurables después de medir. Nunca usar una
   credencial perteneciente a otro RFC; e.firma separada del CSD.
2. Reservar intención persistente, autenticar y solicitar metadatos. Conservar
   identificador proveedor y respuesta. Si se pierde la respuesta, conciliar la
   intención: no enviar a ciegas la misma solicitud.
3. Consultar solicitud con reintentos limitados, backoff y reserva por trabajador.
   Mapear códigos proveedor verificados a estados internos; waiting/failed/expired
   nunca significan vigente/cancelado del CFDI.
4. Recuperar paquetes, validar tamaño/hash y manifiesto; rechazo seguro de ZIP
   peligroso, filas inválidas y duplicados conflictivos. Preservar error por fila.
   Particionar lotes mayores de 1000 filas: máximo del contrato piloto, no del SAT.
5. Normalizar y cotejar RFC/dirección/periodo; insertar documento y observación
   de forma transaccional con unicidad. Repetir paquete no duplica observaciones.
   Observaciones históricas tardías se conservan sin sustituir la última por
   fecha efectiva de observación. Ausencia en un lote no cancela ni borra.
6. Preparar dataset sat_metadata_observation y entregar con outbox/acuse y
   reintentos idempotentes. Un consumidor sólo marca cobertura completa tras
   validar todos los paquetes/recuentos del manifiesto de su partición y periodo;
   el validador de contrato comprueba referencia/hash y recuento declarado, no
   certifica que el productor verificó realmente los bytes.
7. Enriquecer con XML posteriormente, validar su UUID/hash y mantener separados
   metadata, XML, observación SAT y dinero registrado. Crear hallazgos ante
   contradicción; no sobrescribir CFDI, venta, pago o KPI de Cotiza.

## Entrega propuesta y compatibilidad CIMA

sat_metadata.py valida datos normalizados y prepara un sobre JSON puro. Tiene
JSON Schema y ejemplo sintético en contracts/sat-metadata/. Reimportar la misma
observación conserva su clave aunque cambie batch_id. La clave también incluye
dirección para conservar dos observaciones de una autofactura sin duplicar el
documento. El almacén SQLite ya prueba unicidad persistente y transacciones;
la implementación productiva y el receptor CIMA siguen pendientes.
La clave documental permite unir posteriormente XML y estados bajo el mismo
tenant/empresa/RFC/UUID; no crea una propuesta para documentos externos.

| Metadata | CIMA futuro | Disponibilidad |
|---|---|---|
| UUID, RFC y nombres | cfdi_uuid, emisor/receptor | Directa, tras validar contexto |
| Fecha emisión/certificación | fecha_emision/fecha_timbrado | Conservar reloj original; no inventar zona |
| Tipo y total | tipo_comprobante, total | Total decimal textual; no sumar divisas desconocidas |
| Estado/fecha observación | estatus_fiscal/verificado_at/fuente | Observación SAT, no estado comercial ni cobro |
| Moneda, subtotal, descuento, versión | Campos obligatorios cfdi_document v1 | Requiere XML; no fabricar MXN ni cero |
| IVA, relaciones, método, REP | datasets fiscales detallados | Requiere XML; metadata no acredita pagos |
| raw_ref y raw_sha256 | Procedencia | Hash del paquete/fila metadata, nunca presentarlo como hash XML |

El receptor CIMA para este sobre todavía no existe. Antes de conectarlo:
agregar staging tenant/RFC, validar contrato/identidades, registrar acuse y
probar reimportación/concurrencia en base desechable. Prohibido llamar a
insert_venta o incrementar clientes_master desde metadata. No cambia el canónico
v1 existente: es un dataset adicional propuesto en versión 0.1.0.

## Siguientes entregas concretas

1. Adaptador TXT/ZIP y almacén/manifiesto SQLite offline implementados; completar
   migración reversible PostgreSQL implementada en esquemas aislados; falta validar manifiesto multipaquete.
2. Receptor staging CIMA y acuse implementados como función opt-in de laboratorio; falta API autenticada y outbox.
3. Adaptador SAT opt-in; decidir RFC y periodo piloto y resolver custodia cifrada,
   rotación/revocación y acceso auditado a e.firma fuera del chat.
4. Ensayo real autorizado, cobertura comparada y conciliación documental.

Aceptación actual: contratos, importación TXT/ZIP y persistencia local desechable
comprobados con datos sintéticos. Fuera del alcance actual: credenciales,
descarga real, tablas productivas, endpoint de importación, entrega efectiva,
XML enriquecido y conciliación.

## Importador y almacén offline implementados

Perfil acotado sat-metadata-txt-12-v1: UTF-8 con o sin BOM, encabezado exacto de
12 columnas, separador ~, fechas YYYY-MM-DD HH:MM:SS y estado 1/0. Los campos
se cotejaron contra el [fixture del mantenedor PhpCfdi](https://github.com/phpcfdi/sat-ws-descarga-masiva/blob/91521b4c4d07c0b6c2b60c9551b7a6273b38ec21/tests/_files/zip/metadata.txt)
y [DocumentStatus](https://github.com/phpcfdi/sat-ws-descarga-masiva/blob/91521b4c4d07c0b6c2b60c9551b7a6273b38ec21/src/Shared/DocumentStatus.php).
Esto es comprobación técnica de un perfil; no prueba compatibilidad universal
con todos los archivos del SAT. Variantes de encoding/columnas/cuentas de terceros
se rechazan hasta verificarlas. Los ensayos propios no usan documentos del fixture
externo: todos sus RFC, UUID, nombres y cantidades son sintéticos.

- Máximo 2MiB de paquete y contenido expandido, 10 entradas TXT y 1000 filas.
  ZIP se lee en memoria sin extraer; se rechazan rutas, enlaces, cifrado,
  nombres duplicados y relaciones de compresión mayores de 100:1.
- Paquete/manifiesto original y errores por entrada/línea se guardan con hash.
  Una fila inválida o UUID conflictivo rechaza todo el paquete: sólo persiste
  evidencia y errores; no hay escritura parcial de documentos.
- Filas idénticas del mismo paquete se colapsan. Reimportar idénticos bytes
  dentro del mismo alcance devuelve el primer resultado, aunque cambie la hora
  del operador. No equivale a una nueva consulta SAT. El futuro conector debe
  añadir identidad de solicitud y evidencia temporal del proveedor.
- Periodos solapados no duplican una observación ya conocida. Un nuevo estado
  agrega observación; la proyección actual sólo avanza por fecha UTC de observación.
  Conflicto de importe/datos inmutables, o dos estados distintos en el mismo
  instante, se bloquea. Una ausencia en el archivo no borra ni cancela nada.
- La base exige raíz explícita existente y sufijo .sat-metadata-test.sqlite3,
  rechaza enlaces y bases ajenas, usa permisos 0600, claves únicas, claves
  foráneas y BEGIN IMMEDIATE. Dos conexiones se serializan; fallo SQL revierte
  paquete, documento y observación. No carga DATABASE_URL ni credenciales.
- Lecturas por empresa/RFC/tenant y filtro de dirección/periodo; el contexto
  aquí lo entrega un operador de pruebas. No demuestra autenticación ni permisos
  de una futura API. Los hashes detectan alteración accidental; no son firma ni
  sellado de tiempo, y el archivo SQLite no es custodia productiva.
- Cobertura siempre partial, incluso en un TXT sólo con encabezado. No existe
  manifestación de periodo completo por procesar un archivo manual.

Reproducir: `.venv/bin/python -m pytest -q tests/test_sat_metadata_contract.py tests/test_sat_metadata_import.py`.
El caso CLI genera archivo y scope sintéticos en tmp_path e importa dos veces
mediante scripts/import_sat_metadata_offline.py. `--help` lista contexto, periodo
en JSON, ruta de base desechable y fecha observada; no admite URL de base remota.
La consola sólo muestra estado, claves, recuentos y errores sin valores de filas.

El JSON Schema y ejemplo CIMA conservan versión 0.1.0 de propuesta no publicada;
el ejemplo se actualizó por la inclusión de dirección en la clave de observación.

## PostgreSQL y entrega conectada a CIMA en laboratorio

sat_metadata_staging.py comparte las operaciones de datos; los adaptadores SQLite
y PostgreSQL sólo resuelven sesiones y serialización. PostgreSQL exige loopback,
base exacta sat_metadata_test, sin parámetros alternativos en DSN, y esquema
sat_md_test_<uuid hex>. El instalador es explícito, no está en Alembic ni en el
arranque. Up repetido conserva filas; down sólo revierte tablas de su versión,
sin CASCADE. Objetos ajenos o dependencias impiden la reversión. El ensayo usa
UTF-8; no toca public ni tablas productivas. La reserva transaccional serializa
todo el esquema de prueba; no se presenta como una estrategia de escala productiva.

El receptor pertenece a CIMA, módulo utils/sat_metadata_staging.py en
B10sp4rt4n/fradma_dashboard3, con esquemas cima_md_test_<uuid hex> separados de
Digestor. Valida el JSON Schema versionado, contexto de empresa y claves antes
de escribir, almacena observaciones y relaciones de lote, y confirma después de
commit. Repetir un lote devuelve exactamente el mismo acuse; un lote nuevo puede
referir observaciones existentes sin duplicarlas. Mismo ID de lote con contenido
distinto se rechaza. Hay pruebas con dos conexiones, pérdida de acuse, reapertura,
fallo SQL y hashes alterados. No alimenta ventas/clientes/CxC/KPIs.

El ensayo conectado usa código de ambos repositorios y transporte JSON serializado,
con PostgreSQL real en dos esquemas. No usa HTTP ni credenciales de clientes: aún
no hay endpoint ni resolución de membresías de una API. El CI privado de CIMA
fija un commit público de Digestor, sin pedir acceso privado desde Digestor.

Compatibilidad del borrador 0.1.0: fechas de emisión/certificación/cancelación usan
el formato JSON Schema custom source-datetime para preservar la hora original;
observed_at sí exige zona. CIMA registra explícitamente ambos validadores.
Las claves usan JSON UTF-8 ordenado por claves, sin espacios, con null explícitos:
un reordenamiento de propiedades en transporte no cambia la identidad. El contrato
no está publicado a consumidores productivos; los ejemplos/esquemas se actualizan
juntos y se comparan entre repositorios.

Siguiente: manifiestos multipaquete, outbox/acuse verificable en Digestor y endpoint
CIMA protegido por identidad de servicio y membresías, antes de habilitar e.firma
y descarga real. Este avance no cierra la sincronización productiva de pasos 7/8.

### Ensamblado multipaquete de lectura implementado

sat_metadata_manifest.py coteja una declaración explícita de 1–10 paquetes contra
los recibos persistidos del mismo tenant/empresa/RFC/dirección/periodo. Comprueba
integridad de los bytes al leer, estado validado, SHA256 y recuentos de cada
paquete. Faltantes, rechazos y discrepancias bloquean todos los registros de salida.
La declaración no se descubre automáticamente ni se toma de nombres de archivo.

Solapamientos idénticos conservan un documento con todas sus referencias de origen.
Dos estados distintos del mismo UUID dentro del mismo conjunto se rechazan como
conflicto: no se selecciona uno silenciosamente. Hay límite agregado de 1000
documentos por partición. El orden de paquetes no altera la huella del manifiesto.

package_set_status=complete sólo acredita el conjunto declarado; coverage permanece
partial. Este helper no prueba que el SAT haya finalizado una solicitud, no afirma
que no existan otros paquetes y no se conecta al receptor CIMA ni cambia contratos
de entrega. No persiste un nuevo lote: sólo lee los paquetes conservados. El outbox,
la evidencia del manifiesto del proveedor y el endpoint autenticado siguen pendientes.

### Outbox PostgreSQL de laboratorio

sat_metadata_outbox.py guarda el sobre exacto de un paquete previamente validado
y persistido; no permite encolar un archivo arbitrario ni un paquete rechazado.
Se instala explícitamente en un esquema sat_out_test_<uuid> de sat_metadata_test
loopback, con marcador propio y reversión sin CASCADE. No activa workers o rutas.

Identidad de entrega por empresa/tenant/RFC/lote, payload inmutable y hash.
Reservas con token y vencimiento de 60 segundos; llamada de transporte fuera de
la transacción. Cinco intentos máximo y backoff de 2–32 segundos. Resultado incierto
se reintenta con el mismo sobre; un acuse incorrecto bloquea la entrega.
Un trabajador tardío no puede sobrescribir una reserva nueva ni confirmar una
reserva vencida. Las reservas agotadas se bloquean al reclamarlas.

El acuse CIMA debe coincidir en tenant/empresa/RFC, lote, dataset, versión, hash,
UUID del recibo y recuentos. Se conserva después de la validación; releerlo permite
recuperar la confirmación tras reiniciar Digestor sin reenviar una entrega confirmada.
El transporte de estas pruebas es una función sintética inyectada, no HTTP real;
no se afirma entrega productiva o autenticidad criptográfica del acuse. La identidad
de servicio y la API protegida siguen pendientes. Esta cola encola paquetes individuales;
no transforma el ensamblado multipaquete en cobertura total o lote de entrega.
