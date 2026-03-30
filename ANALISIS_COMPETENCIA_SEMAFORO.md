# Analisis De Competencia (Semaforo)

Fecha: 2026-03-30

## Objetivo
Comparar la propuesta actual de la plataforma frente a tres alternativas del mercado:

1. Suite fiscal/documental especializada.
2. OCR generico + automatizacion.
3. Desarrollo interno (in-house).

## Criterio De Semaforo
- Verde: ventaja clara o capacidad madura.
- Amarillo: capacidad parcial o dependiente de configuracion.
- Rojo: brecha relevante o riesgo alto.

## Matriz Comparativa

| Capacidad Clave | Nuestra Plataforma | Suite Especializada | OCR + Automatizacion | In-House |
|---|---|---|---|---|
| Parsing estructurado de CSF | Verde | Verde | Amarillo | Amarillo |
| Validacion campo por campo (si/no + motivo + sugerencia) | Verde | Amarillo | Rojo | Amarillo |
| Trazabilidad y auditoria inmutable (hash chain) | Verde | Amarillo | Rojo | Amarillo |
| Contrato operativo formal (aprobacion, idempotencia, estados) | Verde | Amarillo | Rojo | Amarillo |
| Outbound confiable (reintentos + DLQ + estado por evento) | Verde | Amarillo | Amarillo | Amarillo |
| Monitoreo SLA/SLO operacional | Verde | Amarillo | Rojo | Amarillo |
| Velocidad de ajuste a procesos del cliente | Verde | Amarillo | Amarillo | Verde |
| Costo total de propiedad a 12-24 meses | Verde | Amarillo | Amarillo | Rojo |
| Time-to-value inicial | Amarillo | Amarillo | Verde | Rojo |
| Riesgo de cumplimiento fiscal operativo | Verde | Amarillo | Rojo | Amarillo |

## Lectura Ejecutiva

### Donde ganamos hoy
- Cumplimiento operativo: auditoria inmutable + trazabilidad de eventos.
- Integracion enterprise: idempotencia, estados, reintentos, DLQ.
- Calidad de dato: validacion completa por campo con evidencia.

### Donde la competencia puede presionar
- Suite especializada: fuerza comercial y posicion de marca.
- OCR + automatizacion: precio inicial bajo y entrada rapida.

### Riesgos a vigilar
- Guerra de precios en OCR commodity.
- Roadmaps de suites grandes copiando capacidades operativas clave.

## Mensaje Comercial Recomendado
No vender solo OCR. Vender control operativo del cumplimiento:

- Menor riesgo regulatorio.
- Menor costo operativo de correcciones manuales.
- Mayor confiabilidad de integracion en produccion.

## Acciones Inmediatas Recomendadas
1. Publicar caso de uso con evidencia de reduccion de errores por campo.
2. Medir y exponer KPI de entrega outbound (exito, latencia, retries).
3. Incorporar endpoint de retry manual para eventos en DLQ.
4. Preparar comparativo de TCO a 12 y 24 meses por segmento de cliente.
