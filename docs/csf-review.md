# Revisión explícita de CSF

`GET /csf/{document_id}` expone `review` con valores extraídos originales, régimen normalizado al catálogo compartido, incidencias, sugerencias separadas y revisión SHA-256 del contenido utilizado. No realiza llamadas IA/PAC ni modifica la fuente por solicitar la revisión.

La normalización reconoce códigos y descripciones exactas del catálogo local. Rechaza RFC genérico/provisional, razón social vacía/provisional, CP mal formado, régimen desconocido o incompatible con el tipo de persona, e idCIF ausente/no numérico. Estas reglas no certifican datos contra SAT.

## Cambio de contrato de aprobación

Consultar la CSF y revisar sus datos antes de enviar:

```json
{
  "confirm_review": true,
  "expected_revision": "copiar review.revision de GET /csf/{document_id}",
  "notes": "Datos revisados contra el PDF"
}
```

`POST /v1/documents/{document_id}/approve` devuelve 428 sin confirmación/revisión, 409 si cambió la revisión y 422 ante incidencias. Bloquea la fila durante la comprobación/aprobación y registra la revisión confirmada en auditoría. Al aprobar, los campos principales se actualizan exclusivamente a los valores originales/normalizados revisados, incluyendo recuperación de extracción original en registros históricos con autoaplicación IA. Requiere actualizar consumidores que antes enviaban `{}`.

## IA y extracción

- La persistencia conserva la extracción; no sustituye campos principales con `corrected_json` de IA.
- Ingesta PDF/ZIP y jobs usan `use_ai=false` por defecto. El fallback/corrección IA sólo se ejecuta con opt-in explícito y configuración existente. Cotiza envía false.
- Se corrige el fallback regex de régimen sin grupo capturante y se arma el nombre de persona física desde nombres y apellidos.
- No hay migración ni backfill. No cambia operaciones de timbrado/cancelación/REP.

## Integración

Publicar este backend antes de la interfaz de revisión de Cotiza. La confirmación de valores en Cotiza no llama al endpoint de aprobación outbound ni modifica los datos extraídos de Digestor.

Pruebas: PDF sintético → handlers HTTP reales → job/worker → SQLite temporal → consulta de revisión → aprobación explícita y auditoría. Se bloquean red, IA y PAC en esas pruebas. Las comprobaciones de concurrencia de PostgreSQL dependen del servicio de prueba correspondiente.
