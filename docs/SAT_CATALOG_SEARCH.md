# Catálogos SAT y clasificación asistida

El buscador consulta una copia local del XLS oficial CFDI 4.0 publicado el
2026-11-06. El manifiesto contiene origen, fecha, conteos y SHA-256 del original,
XSD, base e índice comprimido. Son 52,513 productos/servicios y 2,418 unidades.
Las fechas de vigencia se evalúan en America/Mexico_City. La descripción del
catálogo es oficial; la expansión EDR/ThreatDown → software/seguridad es una regla
local de recuperación, no una resolución fiscal del SAT ni una probabilidad.
No hay modelos, embeddings, APIs de pago o llamadas al PAC en la búsqueda.

El XLS conserva unidades de caso mixto (Ohm, Gd, X1w). El alias Dx → DX está
explicitado porque el XSD SAT exige esa representación XML. No se capitalizan
indiscriminadamente las unidades. El constructor rechaza claves ausentes del XSD.

## Actualización reproducible

1. Revisar el vínculo de catálogos en el portal oficial Anexo 20:
   http://omawww.sat.gob.mx/tramitesyservicios/Paginas/anexo_20.htm
2. Descargar el XLS y el XSD oficiales. Registrar publicación y consulta reales.
3. Instalar requirements.catalog.txt solamente en el entorno de mantenimiento.
4. Ejecutar:

```sh
python scripts/build_sat_catalog.py /ruta/catCFDI_V_4_FECHA.xls \
  --source-url URL_OFICIAL --published AAAA-MM-DD --checked AAAA-MM-DD \
  --xsd /ruta/catCFDI.xsd
```

5. Revisar conteos, fechas, diferencias y manifiesto; ejecutar la suite offline.
6. Publicar el snapshot revisado junto con el código. No hay actualización
   silenciosa en ejecución. La UI expone la fecha del snapshot; una nueva versión
   invalida recibos previos de clasificación y exige revisión explícita.

GET /v1/sat-catalogs/search, GET /status y POST /validate requieren autenticación.
Las consultas usan parámetros SQLite, FTS saneado y límites de tamaño/resultados.
Una copia corrupta o no disponible bloquea la validación. El timbrado vuelve a
validar existencia y vigencia antes de reservar y antes de formar el XML.

Cotiza confirma ambas claves con actor, tenant, propuesta, número de partida,
revisión y versión del catálogo. Conserva el acto en proposal_audit_events.
La memoria reutilizable está limitada al tenant, SKU (o concepto exacto sin SKU),
naturaleza comercial y duración. Se recupera como candidato: siempre se revisa y
confirma otra vez. No se modifican XML timbrados ni documentos históricos.
Comprar al fabricante extranjero no determina la clave de lo que se vende al
cliente; deben distinguirse licencia, servicio administrado, implementación y
soporte. El uso CFDI del receptor sigue siendo un dato independiente.
