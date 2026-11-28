# CDC: base común de experiencia, versión 1

## Entrega de esta etapa

- Contrato de marca, módulos, áreas y colores con contenido idéntico en los tres repositorios.
- Encabezado CDC Suite con módulo y empresa o ámbito de empresa de la sesión.
- Cotiza: áreas Inicio, Comercial, Fiscal, Inteligencia y Administración según capacidades y rol; conserva las rutas existentes.
- Cotiza Fiscal: acceso a propuestas ganadas mediante identificador exacto, visibilidad del propietario y límite del lector existente. No es un universo de CFDI.
- CIMA: Centro de la suite en Inicio para administradores; reportes en Inteligencia; CFDI en Fiscal; usuarios, configuración y laboratorio en Administración.
- Digestor: misma imagen en su centro de carga autenticado.
- Autenticación y autorizaciones de los servicios de origen se conservan. No se habilitan empresas o módulos nuevos.

La entrega es una base de experiencia compartida. No acredita una sesión única entre las tres aplicaciones, administración centralizada de licencias ni sincronización automática.

## Contrato compartido

Las copias del JSON v1 son iguales en:
- DynamiQuote: `cotiza-web/lib/suite/experience.json`.
- fradma_dashboard3: `config/suite_experience.json`.
- digestor__fiscal: `config/suite_experience.json`.

Los encabezados consumen el contrato. Las copias se versionan juntas hasta extraer un paquete compartido cuando el despliegue lo justifique. Esta etapa no añade infraestructura ni dependencias.

## Responsabilidades

| Dominio | Autoridad de origen |
| --- | --- |
| Operación comercial, propuesta y revisión | Cotiza |
| Procesamiento y actos fiscales | Digestor Fiscal |
| Modelos de ventas, cartera e indicadores | CIMA |
| Identidad de la persona | Clerk, con el vínculo local autorizado de cada servicio |

Los identificadores locales de empresa no se presumen iguales. La navegación no concede permiso. La ausencia de un módulo en el menú tampoco sustituye la autorización de sus rutas.

## Siguiente etapa: acceso compartido

Inventario observado en el código:
- Cotiza usa Clerk y registros locales; su rama activa conserva caminos de compatibilidad para reconciliar identidades. Antes del acceso único deben revisarse y sustituirse por vínculos inmutables y membresías activas verificadas, con una migración de cuentas probada.
- CIMA admite OIDC de Clerk y conserva empresas, membresías y roles en su base.
- Digestor valida JWT propios; el cliente de Cotiza mantiene una conexión fiscal del servidor.

Antes de activar el acceso único:
1. Verificar que Cotiza y el cliente OIDC de CIMA usan la autoridad de identidad prevista, sus dominios y redirecciones exactas.
2. Preparar un mapa explícito y revocable de identidad y empresa entre los servicios. No vincular por coincidencia de nombre, correo o RFC durante una solicitud.
3. Definir una autoridad para módulos contratados, con ausencia de habilitación igual a acceso denegado. Mantener separados licencia, membresía y permisos.
4. Resolver el acceso delegado a Digestor con identidad de actor y empresa verificadas en servidor; no compartir su cuenta de integración con el navegador.
5. Conservar el vínculo exacto propuesta → borrador → UUID en el recorrido de SynAppsSys.
6. Validar cierre de sesión y revocación en cada aplicación antes de declarar sesión única.

No se cambiarán proveedores de identidad ni se reasignarán empresas como consecuencia de la actualización visual.

## Aceptación de acceso

| Caso | Resultado esperado |
| --- | --- |
| Usuario ordinario | Sólo su empresa y sus operaciones permitidas |
| Usuario sin membresía activa | Sin acceso a datos |
| Administrador de empresa | Administración local, sin listado transversal de empresas |
| Superadmin | Cambio explícito de contexto y registro del acto |
| Módulo deshabilitado | Sin acceso por menú ni por URL/API |
| Revocación durante una sesión | La siguiente solicitud protegida vuelve a comprobar permiso |
| Dos empresas en ventanas distintas | Ninguna consulta hereda el contexto de la otra |
| Selección de una propuesta | Conserva el ID exacto hasta documento y análisis |

## Publicación y reversión

Los cambios se preparan sobre `main` de Cotiza y Digestor, y `feat/rep-import-sat-sync` de CIMA, su rama activa observada. No requieren migración ni modificación de credenciales. Revertir los commits de esta etapa restaura la experiencia anterior sin alterar propuestas, XML, pagos ni membresías.

La revisión de producción deberá comprobar un administrador y un usuario ordinario, la empresa visible y el recorrido de SynAppsSys. La apariencia común se valida separadamente de la integración funcional y de la sesión única.

