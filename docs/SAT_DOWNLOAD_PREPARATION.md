# Descarga SAT con e.firma — preparación del piloto SynAppsSys

## Alcance real

Este cambio prepara adquisición de paquetes de CFDI emitidos y metadatos desde
el WS del SAT, con estado persistente local. No está montado en FastAPI ni en
Railway, no tiene credenciales y no se inicia al importar la aplicación.
No escribe tablas financieras, no timbra, no cancela, no entrega a CIMA y no
cierra la conciliación bancaria o fiscal. `acquired` significa paquete guardado,
no documento importado ni saldo conciliado. La vigilancia de ChatGPT existente
es independiente y no ejecuta este worker.

RFC único permitido: SAS2108197G0. No se aceptan credenciales de otros tenants.
Los ejemplos no contienen certificados, contraseñas o documentos reales.

## Preparación segura de credenciales

En un entorno Linux dedicado, colocar certificado DER, llave DER cifrada y
contraseña en tres archivos de secretos separados, propiedad del usuario del
worker, permisos 0600, directorio 0700. No subir a Git, ChatGPT, logs o artefactos
CI. No usar argumentos de línea de comandos para la contraseña. El archivo de
contraseña admite un salto de línea final convencional; el secreto no se imprime.
Los tres archivos sólo se leen en memoria. Evitar directorios compartidos y
enlaces simbólicos; cifrado de volumen, backups y custodia deben definirse antes
de montar datos reales. No se ha creado una pantalla de carga de secretos.

Variables propuestas (nombres, nunca los valores de secretos):

```
SAT_DOWNLOAD_ENABLED=0
SAT_DOWNLOAD_RFC=SAS2108197G0
SAT_EFIRMA_CER_FILE=/run/secrets/sat/certificate.der
SAT_EFIRMA_KEY_FILE=/run/secrets/sat/private-key.der
SAT_EFIRMA_PASSWORD_FILE=/run/secrets/sat/password
SAT_DOWNLOAD_STAGING_DIR=/data/sat-acquisition
```

Con las variables configuradas, ejecutar `python scripts/sat_download.py preflight`.
No llama al SAT: comprueba llave/certificado, RSA, vigencia y RFC; devuelve RFC,
vencimiento, huella pública y el resultado local. No verifica cadena de confianza
ni revocación. El SDK exige además su clasificación FIEL antes de crear el cliente,
que es una clasificación del SDK y no sustituye una validación de cadena.

## Recuperación propuesta y operación

Instalar el archivo opcional `requirements.sat-download.txt` en el worker aislado.
Su SDK se fija al commit revisado; no se modifica requirements.txt del servidor.
Antes del despliegue, resolver dependencias y verificar compatibilidad contra el
SDK real; las pruebas locales usan respuestas simuladas, sin SDK instalado.

Ejemplo de recuperación inicial, con solapamiento respecto al último documento
de CIMA (08/06/2026):

```
python scripts/sat_download.py plan --from-date 2026-06-01 --to-date 2026-11-25
python scripts/sat_download.py status
```

Una solicitud por día/tipo: XML de vigentes y Metadata de Todos. No filtra por
tipo de comprobante: también debe recuperar CFDI tipo P para sus relaciones REP.
La consulta metadata es necesaria para conservar evidencia de cancelaciones;
no debe inferirse cancelación porque el XML no aparezca en una descarga.

Sólo después de aprobar el montaje seguro y autenticar un ensayo real, habilitar
`SAT_DOWNLOAD_ENABLED=1` y programar `python scripts/sat_download.py tick` cada
15 minutos. Cada ejecución procesa a lo sumo un trabajo. Repetir `plan` para el
mismo rango no duplica trabajos. Para días nuevos, agregar rangos de días cerrados.
El planificador diario incremental aún debe conectarse al supervisor; este PR
no instala un cron ni asegura revisión futura de cancelaciones históricas.

El SAT es asíncrono: guardar IdSolicitud, consultar, descargar paquetes al terminar.
Estados locales: planned, submitting, waiting, acquired, blocked,
submission_uncertain. Si una solicitud pierde su acuse, NO se reenvía a ciegas:
queda pendiente de recuperación manual para evitar agotar solicitudes idénticas.
Errores de consulta/descarga reintentan con backoff de 15 minutos hasta seis horas.
Límites SAT de tamaño/día necesitan partir la ventana si fuera necesario; no se
subdivide automáticamente una respuesta de exceso. No hay recuperación automática
de solicitudes rechazadas/vencidas.

Un volumen privado persistente y un solo worker. flock excluye procesos locales,
no es una reserva distribuida entre hosts. SQLite guarda solicitud, paquetes,
hashes y fecha de adquisición; los ZIP se guardan de forma atómica por SHA256 y
no se extraen. Se limita respuesta/ZIP/expansión/número de archivos, se revisa CRC,
se rechazan ZIP cifrados y conflictos de contenido de un mismo paquete. Esto no
deduplica todavía UUIDs contenidos en paquetes diferentes.

## Pendiente para producción y CIMA

1. Elegir montaje de secretos y volumen; validar e.firma real sin imprimirla.
2. Ensayar autenticación y una ventana de un día; cotejar RFC y respuesta SAT.
3. Integrar los paquetes con importación XML I/P y metadatos: UUID, relaciones,
   moneda, parciales, procedencia y estados, sin derivar cobros por ausencia de REP.
4. Conectar outbox/autorización/transporte de PR27/28 y receptor CIMA PR59/65,
   respetando delegación por empresa/membresía y acuses idempotentes.
5. Reconciliar AF117 y un documento cancelado; dos ejecuciones y reinicio deben
   conservar conteos sin duplicar. Ensayar duplicados de UUID y REP cancelado/sustituido.
6. Instalar supervisor y plan incremental; mostrar en CIMA última solicitud,
   adquisición, importación y conciliación como fechas distintas. No mostrar
   sincronizado con sólo paquete acquired ni con un rango incompleto.

## Verificación

`python -m unittest discover -s tests -p test_sat_download_preparation.py -v`

19 pruebas locales: certificado/llave sintéticos, RFC distinto, expiración,
contraseña incorrecta, permisos, solicitudes ambiguas, reinicio, espera,
rechazo SAT, paquetes incompletos, hash conflictivo, ZIP inválido y bloqueo local.
Sin SAT/PAC, e.firma real, red externa, Neon o cambios de producción.

Fuentes técnicas consultadas:
- https://github.com/SAT-CFDI/python-satcfdi/blob/202a813ac91934019c9d028a8130428b54473238/docs/pages/getting_started/45_cfdi_descarga_massiva.rst
- https://github.com/SAT-CFDI/python-satcfdi/blob/202a813ac91934019c9d028a8130428b54473238/satcfdi/pacs/sat.py
- https://www.sat.gob.mx/cs/Satellite?blobcol=urldata&blobkey=id&blobtable=MungoBlobs&blobwhere=1461175779527&ssbinary=true
