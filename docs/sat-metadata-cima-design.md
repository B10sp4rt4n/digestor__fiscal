# Preparación SAT → Digestor → CIMA · contrato 0.1.0

Estado: propuesta ejecutable offline. No hay conector de descarga, tablas nuevas,
rutas HTTP, sincronización real ni receptor CIMA activado. La preparación de los
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
observación conserva su clave aunque cambie batch_id. Una nueva consulta con
otra fecha es otra observación. No hay deduplicación persistente implementada.
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

1. Almacén/manifiesto y adaptador TXT offline con fixtures sintéticos y prueba de
   paquete repetido/conflictivo; después migración reversible en base aislada.
2. Receptor staging CIMA con aislamiento y claves únicas, sin tocar KPIs.
3. Adaptador SAT opt-in; decidir RFC y periodo piloto y resolver custodia cifrada,
   rotación/revocación y acceso auditado a e.firma fuera del chat.
4. Ensayo real autorizado, cobertura comparada y conciliación documental.

Aceptación actual: contratos puros, comparación con CIMA y reglas comprobadas
localmente. Fuera del alcance actual: credenciales, descarga real, tablas,
endpoint de importación, entrega efectiva, XML enriquecido y conciliación.
