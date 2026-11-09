# Catálogo de usos CFDI

`app/data/sat-uso-cfdi.json` contiene las 24 claves CFDI 4.0 y sus relaciones explícitas por régimen y tipo de persona. `fiscal_catalogs.USOS_CFDI` alimenta el validador de receptor y `GET /v1/receptor/catalogos`; conserva el formato de respuesta existente.

Se corrigen listas anteriormente abiertas a todos los regímenes para G01/G03/inversiones, se mantiene la excepción G02/616 y se ajustan las deducciones personales: incluye 607/615 y excluye 616/621/626. CP01 conserva la compatibilidad amplia para complementos de pago; CN01 corresponde a persona física en 605. No modifica la generación o timbrado de documentos.

El snapshot se refleja idéntico en `DynamiQuote/cotiza-web/lib/data/sat-uso-cfdi.json`; actualizar ambos con pruebas de límites. Consultado el 9 de noviembre de 2026: regla SAT 2.7.1.29 (https://wwwmat.sat.gob.mx/articulo/68429/regla-2.7.1.32) y documentación de integración https://blog.gigstack.pro/post/usocfdi-cfdi-40-catalogo-regimen-receptor-2026. La referencia XLS SAT indicada en el JSON respondió 503; la revisión se apoya en la documentación publicada, sin presentar una descarga directa como realizada. La validación local de compatibilidad no acredita por sí sola la deducibilidad de una operación.
