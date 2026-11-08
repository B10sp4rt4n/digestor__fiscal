# REP: XSD fijados, límites fiscales y conservación del cierre

## Cambio verificable

Antes de preparar/enviar un complemento se valida su estructura y el perfil MXN/IVA16 soportado. La respuesta del PAC conserva la comparación contra la solicitud y además debe pasar los XSD originales de CFDI4.0, Pagos2.0 y Timbre1.1. Una respuesta inconsistente queda unknown; no se confirma un recibo sin esos controles.

Los seis recursos oficiales están empaquetados en app/resources/sat/rep20-schemas.json.gz. manifest.json conserva URLs, fecha UTC de recuperación, tamaño y SHA-256 de cada recurso exacto (incluido BOM si existe) y del paquete comprimido. Incluye catCFDI, catPagos y tdCFDI. La resolución de imports sólo permite esos recursos en memoria, con red y entidades externas deshabilitadas. No se descarga nada al validar un documento. El script refresh_sat_rep_schemas.py actualiza deliberadamente el paquete; sus cambios requieren revisar hashes y volver a probar antes de publicar.

Los catálogos son una instantánea recuperada del SAT, no una consulta de vigencia en vivo. Se comprueba pertenencia donde los XSD la exigen y adicionalmente código postal del receptor. No se verifica RFC contra el padrón SAT ni compatibilidad de regímenes/uso con el contribuyente real.

## Preliminar y respuesta firmada

validate(..., signed=False) identifica mode=unsigned_structure. Usa una derivación explícita del esquema CFDI que permite omitir únicamente Sello, NoCertificado y Certificado; los XSD originales de Pagos y catálogos permanecen intactos. El XML del usuario no recibe valores ficticios y el XML preliminar no se marca como timbrado.

signed=True exige los esquemas originales y un TimbreFiscalDigital con campos estructuralmente válidos. Esta validación no acredita firma criptográfica, vigencia del CSD, autorización del certificador, estado/cancelación del CFDI ni custodia externa. Las respuestas de prueba llevan campos sintéticos identificados en los fixtures, nunca certificados reales. El reporte indica signature_verified=false, taxpayer_verified=false y live_catalogue_verified=false.

Se validan reglas adicionales del perfil: tipoP, exportación01, monedaXXX, total/subtotal0; ausencia de método/forma/tipo de cambio/descuento en el comprobante; usoCP01; concepto único Pago/84111506/ACT/objeto01 sin hijos; un pago MXN y un documento relacionado MXN, equivalencia/tipo de cambio1, aplicación completa del importe, saldo anterior-pagado=insoluto, fechas y trasladosDR/P/totales consistentes. Se rechazan complementos ajenos al perfil y retenciones. No se declara cubierta toda la matriz SAT ni todos los perfiles fiscales.

## Redondeo y cierre

Se reemplaza el margen fijo de0.000002 por los límites del estándar SAT Pagos2.0, páginas48–49:
- Inferior: truncar (BaseDR - 10^(-decimalesBaseDR)/2) * tasa a los decimales escritos en ImporteDR.
- Superior: redondear hacia arriba (BaseDR + 10^(-decimalesBaseDR)/2 - 10^-12) * tasa a esos mismos decimales.

La escala escrita importa: 16.00 y 16.000000 no tienen el mismo margen admisible. El ejemplo base100.010000/impuesto16.00 permite representar una factura de base100.01/IVA16.00 que antes podía quedar bloqueada por el margen fijo.

Las nuevas declaraciones usan allocation_version=rep20-cumulative-tax-cents/2: base acumulada a6 decimales e impuesto a centavos. Cada asignación respeta los límites del abono y reserva un remanente que puede liquidarse inmediatamente dentro de esos límites. La última parcialidad consume exactamente base e IVA restantes; saldo monetario queda0 y la suma de impuestos coincide con el XML original. La evidencia conserva campos de6 decimales sin alterar su contrato; al generar XML nuevo se escribe ImporteDR/P con2 decimales según la versión declarada.

Los historiales anteriores no se reescriben. Si ya contienen impuesto fraccionario inferior a centavo que no admite la nueva distribución, se bloquea el registro siguiente y se requiere revisión fiscal explícita. También se rechazan perfiles agregados de múltiples partidas cuyos redondeos originales no permiten satisfacer simultáneamente límites y conservación: no se ajustan impuestos por intuición para lograr un timbre.

## Sandbox y siguiente prueba

Se fuerza entorno sandbox y destino HTTPS pruebas.timbracfdi33.mx, puertos443/1444. La etiqueta de entorno sola no basta: el valor SOAP heredado podía apuntar al servidor productivo. Ese destino ahora queda bloqueado en PPD y pagos, incluso si la etiqueta dice sandbox.

Inspección exclusivamente local realizada: flags PPD/REP apagados, destino REST de pruebas reconocido, sin token activo ni token específico de sandbox disponible. No se consultó configuración remota ni se contactó al PAC. No es evidencia de ausencia de credenciales en otras instalaciones.

Comandos sin red ni escrituras a base:

```sh
python scripts/inspect_rep_sandbox.py
python scripts/rep_preflight.py complemento-preliminar.xml
python scripts/rep_preflight.py respuesta-pac.xml --signed
```

Para el ensayo con proveedor faltan credencial sandbox disponible por configuración segura y confirmación del emisor/CSD de pruebas y del contrato de sellado del adaptador. Después ejecutar en base aislada: factura PPD → abono → REP1 aceptado → liquidación → REP2 aceptado, conservando solicitudes/respuestas y hashes. No activar flags ni migrar producción por este cambio. Recuperación externa de unknown, correcciones/cancelaciones y PDF REP siguen pendientes.

## Verificación

Pruebas offline cubren mutaciones de importes, saldos, campos prohibidos, claves/código postal, estructura firmada/preliminar, entidades externas y destino productivo camuflado como sandbox. Prueban405 bases con particiones aleatorias reproducibles y una liquidación válida tras cada paso, además de999 pagos, sumas exactas y bloqueo de historiales/perfiles no representables. Se mantiene la suite de concurrencia PostgreSQL y proveedor simulado. CI es evidencia de software; no sustituye aceptación de PAC ni validación fiscal real.

Referencia normativa consultada: estándar oficial Pagos2.0, sección4, páginas38–50:
https://wwwmat.sat.gob.mx/cs/Satellite?blobcol=urldata&blobkey=id&blobtable=MungoBlobs&blobwhere=1461175070885&ssbinary=true
