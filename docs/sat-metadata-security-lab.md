# Acuses autenticados para el piloto sintético

Extensión aislada de PR27. No monta rutas, inicia workers, publica servicios o
habilita el vínculo real con CIMA. El importador y el contrato de metadatos 0.1.0
permanecen iguales; el almacenamiento desechable outbox sube a 0.3.0 y rechaza
esquemas de laboratorio anteriores. No constituye una migración productiva.

El piloto utiliza `hardened_lab_http_transport`, con clave de respuesta distinta
de la clave de solicitud y UUID de empresa CIMA esperado explícito. No admite
claves/destinos ausentes ni desciende al transporte legado. El helper original
se conserva para compatibilidad y pruebas antiguas; no es el cliente del piloto.

La solicitud conserva el protocolo HMAC local existente. La respuesta agrega
`X-Response-Signature`, HMAC-SHA256 con dominio `cima-metadata-lab-response-v1`
y componentes, separados por LF, en este orden:

1. dominio;
2. service_id;
3. POST;
4. /v1/staging/sat-metadata;
5. timestamp de la solicitud;
6. nonce de la solicitud;
7. status HTTP decimal;
8. SHA256 de bytes exactos de la solicitud;
9. SHA256 de bytes exactos de la respuesta.

No hay LF final. Es un protocolo interno de laboratorio, no una implementación
de RFC9421. Se rechazan firma ausente/duplicada/incorrecta, respuesta alterada,
firma de otro intento o destino inesperado. El acuse añade cima_tenant_id,
cima_empresa_id y authorization_sha256; la outbox sigue validando lote, origen,
hash del payload, versión, dataset y recuentos. El sobre de origen no se reescribe.

El cuerpo del recibo es estable; su firma de transporte cambia en cada intento.
`VerifiedLabAcknowledgment` mantiene el cuerpo y una prueba separada de la firma
verificada. La outbox conserva esa prueba y su hash en la misma confirmación con
reserva/token: solicitud referenciada por hash, respuesta exacta, firma, servicio,
timestamp y nonce. No guarda claves. Permite cotejar después la firma con la clave
de verificación correspondiente sin confundir nuevo intento con un nuevo recibo.

Las claves de los ensayos son sintéticas o aleatorias efímeras. Falta custodia,
identificación/rotación operativa de claves, TLS y autorización de despliegue.
HMAC compartido no aporta no repudio ni sellado de tiempo. Este cambio no afirma
cumplimiento SAT, cobertura completa, pagos, cartera o KPIs.

La CI dedicada corre las pruebas de recibos firmados junto con la suite existente
en PostgreSQL17 desechable. Las pruebas conectadas viven en el PR de CIMA y deben
fijar el commit exacto de este productor. Nunca usar secretos de cuentas reales.
