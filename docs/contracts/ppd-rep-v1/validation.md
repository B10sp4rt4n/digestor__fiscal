# Evidencia y límites de validación

Revisión sobre los SHA registrados en source-manifest.json. `validation.log`
contiene ejecución del modelo y schemas, sin sockets de aplicación ni PAC.

- Modelo aislado: **37 tests**, cero fallos/omitidos. Ejecutado en ambos repos;
  son las mismas37 pruebas, no74 escenarios distintos.
- JSON Schema2020-12: **13 tests**, cero fallos/omitidos; jsonschema4.25.1 en
  dependencia temporal fuera de repos/app. Schema request/result y ejemplos.
- Guard existente Digestor: **3 assertions** con módulo fiscal_rules real,
  importado por ruta sin app/config: PUE permitido, PPD99 bloqueado.
- Cotiza: **22 tests existentes** en lib/validations/proposal-invoice.test.ts y
  lib/integrations/digestor-fiscal.test.ts pasaron, Vitest4.1.5, fetch simulado.
  Usaron dependencias locales disponibles; no se instaló ni modificó el lockfile.
  Comando desde cotiza-web: `node node_modules/vitest/vitest.mjs run
  lib/validations/proposal-invoice.test.ts lib/integrations/digestor-fiscal.test.ts`.
- La primera ejecución Vitest desde la raíz falló por resolución del alias `@`;
  ejecutar desde cotiza-web cargó la configuración correcta y pasó.
- La primera prueba del schema detectó que el checker de formato de fecha no
  imponía zona sin dependencias opcionales; se agregó patrón explícito de zona,
  y las13 pruebas finales pasaron. El servidor igualmente debe parsear calendario.

Alcance de cambios: únicamente docs/contracts/ppd-rep-v1 y
scripts/ppd_rep_contract. Ningún archivo de aplicación, ruta, modelo ORM,
migración, configuración, dependencia productiva o workflow cambió. Pruebas
reducidas prueban contrato y regresión elegida; no acreditan toda la suite previa.

No se ejecutaron PostgreSQL, DDL, builder REP, XSD, firmas ni consultas reales
SAT/PAC/banco. No se emitieron XML/CFDI ni se usó un PAC sandbox. No despliegue,
merge o habilitación productiva. Consultas web públicas sólo para documentación.

Pendiente: vigencia normativa posterior a compilación de julio2026, validación
completa de guía/matriz/catálogos/XSD, diseño final de autorización por rol,
correcciones/cancelaciones externas, SQL/concurrencia/fencing reales, inyección
de fallos en handlers y coordinación productiva. Ver acceptance-matrix.md.
