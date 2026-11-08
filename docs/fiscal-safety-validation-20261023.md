# Validación fiscal aislada para Cotiza CRM

Estado: candidato en PR #2; sin merge ni despliegue productivo. La validación de
código y contrato HTTP no certifica el comportamiento del PAC real ni sus sellos.

## Problemas reproducidos y corrección

La regresión sobre a6cc68f reprodujo ocho fallos entre 19 casos: respuestas 5xx,
timeout HTTP o cuerpos incompletos podían clasificarse como rechazo y permitir
otro intento; texto con las palabras TimbreFiscalDigital y UUID podía aparentar
un XML timbrado; evidencia histórica incoherente no bloqueaba siempre edición
ni emisión. No se utilizaron credenciales ni comprobantes reales.

El candidato conserva unknown ante cualquier respuesta que no confirme un XML
CFDI 4.0 estructurado con un único TimbreFiscalDigital y UUID, o un rechazo PAC
explícito reconocido. SOAP conserva los fallos HTTP en vez de convertirlos en
rechazos de negocio. Solo el rechazo reconocido permite corregir/reintentar.
La estructura XML y UUID no constituyen validación criptográfica ni consulta SAT.

El XML previo no nulo, la fecha o los estados timbrados bloquean edición y
reenvío incluso si los demás campos históricos son incoherentes. La reserva
atómica comprueba también ausencia de XML previo en la propia actualización.
El GET de resultado conserva los nombres stamp_status, uuid y xml_base64 que
consume Cotiza; no consulta al PAC ni solicita otro timbre.

## Pruebas

Fuente validada: 074523e5d6647f8b4b92d34a590b13525d966d91, árbol
52cabb5f0bb1b1503cec5eafecfb59a89df82d84.
CI completa **success**, 32 aprobadas y cero omitidas, PostgreSQL 17.11:
https://github.com/B10sp4rt4n/digestor__fiscal/actions/runs/39982889270

- Local: 29 casos aprobados, tres casos PostgreSQL omitidos por falta del servidor
  local. SQLite en memoria, handlers reales y PAC simulado; red requests bloqueada.
- Cotiza CRM: 25 casos aprobados en cliente Digestor y rutas stamp/reconcile sobre
  fuente de PR #28 equivalente al árbol 047a07c3. No hubo conexión fiscal remota.
- PostgreSQL 17: consultar el resultado y SHA exactos en
  docs/evidence/fiscal-safety-validation-20261023.json. Las pruebas usan dos
  conexiones independientes, reserva simultánea tras lecturas sincronizadas,
  edición bloqueada mientras el proveedor procesa y edición previa serializada
  con la reserva. Base ficticia nombrada y host loopback obligatorios; esquemas
  temporales eliminados al terminar. Ninguna URL productiva alimenta ese fixture.
- HTTP real de FastAPI en proceso: 401 sin sesión, 403 para viewer y selección de
  otro tenant, 404 de borrador ajeno, contrato GET correcto y POST 405.
- SOAP sintético: respuesta directa y prefijos escapados, rechazo reconocido,
  fallo de transporte, cuerpo incompleto, namespaces/UUID incorrectos y dos TFD.

La primera definición CI falló al interpretar un literal SQLite sin comillas;
se corrigió en b8817247, sin ejecutar pruebas en esa primera definición. La
siguiente ejecución aprobó 31 casos y falló la expectativa de nombre en el XML
porque el fixture usaba RFC genérico y el constructor lo convierte en PUBLICO
EN GENERAL. Se cambió únicamente ese RFC sintético en 074523e5; no se modificó
esa normalización de negocio ni se debilitó la prueba.

## Conciliación de un resultado unknown

1. Mantener el bloqueo; no regenerar prefactura, reenviar ni cambiar su estado por
   inferencia. Consultar GET stamp-result solo devuelve la evidencia ya guardada.
2. Conservar tenant, draft, id de soporte, hora, respuesta y estado. Un timeout o
   una consulta sin resultado no demuestran que el PAC no emitió el comprobante.
3. Revisar mediante el canal ya autorizado del PAC y recuperar el XML/UUID previo,
   sin enviar nuevamente el CFDI. Contrastar emisor, receptor, importes e identidad
   del borrador antes de proponer cualquier actualización local.
4. Este cambio no crea un endpoint de desbloqueo ni automatiza la conciliación
   manual de unknown. Una recuperación debe revisarse y registrarse preservando
   evidencia anterior. No liberar la reserva solo por falta de UUID.

## Liberación pendiente

Railway tiene digestor__fiscal vinculado a main; el PR utiliza
fix/billing-idempotency-mvp. La lectura posterior al push conservó el deployment
fced17d8-aa0b-4ca1-a1c5-692c138de8e5 en SUCCESS y sin cambios preparados.
No se cambió configuración, credenciales, base, proveedor o XML productivo.

Antes de desplegar: revisar la evidencia, contrastar una respuesta real anterior
redactada sin remitir CFDI, verificar el destino/tenant y preparar recuperación.
El backend compatible debe liberarse antes del CRM. Conservar los bloqueos de
respaldo recuperable, operadores reales y parada/drenado de escritores del
runbook de Cotiza. Sin invitaciones, correo, timbrado real ni servicios nuevos.
