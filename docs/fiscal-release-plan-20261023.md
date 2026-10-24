# Preparación de liberación del Digestor Fiscal

Estado: preparado para revisión, sin merge, despliegue o acceso a datos fiscales.
El PR #2 sigue draft. Este procedimiento conserva el orden Digestor antes de CRM.

## Destino y fuente comprobados

| Elemento | Evidencia de plataforma |
| --- | --- |
| Proyecto Railway | brilliant-gentleness / 911f84ed-a4df-40ec-a122-f77d26da261c |
| Entorno | production / f8cd837c-8769-46c8-9f1f-c84fbbdc7cdc |
| Servicio | digestor__fiscal / ef6c5593-5099-48e0-9da5-b78bcd66a845 |
| Deployment actual | fced17d8-aa0b-4ca1-a1c5-692c138de8e5 / SUCCESS |
| Commit atribuido por Railway | 9e251d761a23dccc554c11ba0a22274fd4f8e269 |
| Fuente configurada | B10sp4rt4n/digestor__fiscal, main; sin cambios staged |
| Candidato de código probado | 074523e5d6647f8b4b92d34a590b13525d966d91; documentación posterior dbb32977 |
| Pruebas | CI 39982889270 success, 32 casos, PostgreSQL 17.11 sintético |

get_deployment_diagnosis devuelve contexto de commit aunque no haya diagnóstico
al ser SUCCESS. Se comprobó que app/main.py, config.py y Procfile no cambian entre
el commit desplegado y el candidato. No hay procesos de fondo nuevos introducidos
por el PR fiscal; deben incluirse los ya existentes en el control del corte.

## Almacenamiento propio: resolver antes de reiniciar

La base del Digestor es independiente de la base Neon del CRM salvo prueba de su
destino exacto. No se ha demostrado que compartan host, base ni esquema.
Railway muestra DB_URL, pero el OAuth del conector devuelve solo nombres, no su
valor. La API de servicio muestra cero volúmenes montados; **no demuestra uso de
SQLite ni almacenamiento efímero**, pues podría apuntar a una base remota.

No liberar hasta confirmar backend/destino y respaldo recuperable propio. Si es
SQLite, conservar base y archivos WAL con un método consistente, verificar si
están en almacenamiento durable y ensayar recuperación antes de redeploy. Si es
PostgreSQL, identificar host/base y realizar una copia consistente con su propia
ruta segura de recuperación. No asumir que el script Neon de Cotiza sirve para
otro backend o una base distinta.

No ejecutar POST /admin/backups/export como sustituto de esta verificación: su
implementación escribe un JSON local sin cifrar y no prueba una copia externa,
durabilidad, restauración o inclusión de usuarios. El endpoint no se invocó.

### Inspección preparada, solo lectura

scripts/inspect_release_storage.py importa únicamente app.core.config. Reporta
backend, host/base o ruta/tamaño de SQLite, flags de procesos de fondo y hashes
de tres fuentes. No importa app.main ni crea engine, no conecta SQL, no consulta
filas, no llama al PAC, no imprime contraseñas ni tokens, no crea claves/archivos.

En un checkout del candidato con su entorno de prueba:

```bash
python -m scripts.inspect_release_storage
```

Antes del despliegue, la instancia antigua aún no contiene este archivo. Para
inspeccionarla sin subir código, un operador autorizado puede copiar la fuente
revisada a Python por stdin en la consola del deployment exacto. No desplegar
primero para disponer del script. Esa ejecución de consola **no se ha realizado**.
Prueba local de la herramienta: URLs ficticias PostgreSQL inaccesible y SQLite
memory producen metadatos sin revelar usuario/contraseña y sin intentar conexión.

## Arranque y drenado

El lifespan actual ejecuta Base.metadata.create_all y seed si no hay usuarios,
aunque el comentario lo describa como desarrollo. Después inicia workers de
documentos y, si SYNC_OUTBOUND_AUTOPOLL, el worker de entregas. Las tablas/colas y
estados deben inventariarse antes de considerar una pausa HTTP suficiente.

La configuración por defecto permite marcar entregado un evento cuando no hay
webhook y SYNC_OUTBOUND_ALLOW_NOOP_TARGET=true. No se leyó el valor efectivo ni
se afirma que exista ese comportamiento en datos productivos. Verificar backlog,
flags y destino autorizado antes del reinicio. No ejecutar el lifespan en una
prueba remota como si fuera una lectura inocua. No cambiar esos flags por inferencia.

## Secuencia revisable

1. Resolver metadatos de almacenamiento y procesos de fondo, sin modificar datos.
2. Capturar y probar recuperación del estado fiscal actual, incluyendo evidencia
   XML/UUID, usuarios y colas necesarias; almacenamiento privado y clave resguardada.
3. Revisar respuesta SOAP real anterior redactada y contrato de consulta sin enviar
   otro CFDI. Mantener unknown bloqueado; aplicar el procedimiento manual documentado.
4. Fijar SHA exacto de liberación y plan de recuperación con reserva/unknown intactos.
   No volver automáticamente al código anterior que permite timbrados repetidos.
5. Tras aprobación concreta de ventana/impacto, cerrar entradas y drenar solicitudes
   fiscales y procesos de fondo. Publicar únicamente el candidato aprobado.
6. Verificar salud, autenticación/tenant y GET stamp-result sobre evidencia existente,
   preservando XML/UUID y sin llamar a POST stamp ni crear una factura para probar.
7. Mantener el CRM en Preview hasta completar sus propios requisitos de corte.

No se requiere un nuevo servicio o suscripción para preparar estos controles.
La ubicación de recuperación y su custodia siguen pendientes de definición.
