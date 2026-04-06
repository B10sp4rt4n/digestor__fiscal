# Digestor Fiscal — Hito: Pipeline de Facturación Inteligente Completo

**Fecha:** 5 de abril de 2026  
**Estado:** ✅ Validado en entorno de pruebas SAT

---

## ¿Qué logramos?

Por primera vez, el sistema ejecutó el ciclo completo de facturación electrónica de forma autónoma:

> **CSF real del cliente → Validación automática → Enriquecimiento con IA → Prefactura visual → CFDI timbrado y sellado por el PAC**

Sin intervención manual. Sin errores. Con un CFDI válido ante el SAT.

---

## El pipeline en detalle

```
1. Cliente sube su Constancia de Situación Fiscal (PDF)
         ↓
2. El sistema extrae automáticamente RFC, razón social,
   régimen fiscal, CP, domicilio, CURP, idCIF
         ↓
3. La IA valida y corrige cada campo individualmente
   (OCR mal leído, texto pegado, formatos incorrectos)
         ↓
4. Los datos corregidos se persisten en base de datos
         ↓
5. El usuario selecciona productos del catálogo
   y revisa la prefactura visual antes de emitir
         ↓
6. Se genera el XML CFDI 4.0 con todos los campos
   fiscalmente correctos
         ↓
7. El PAC timbra y sella el CFDI ✅
```

**CFDI de demostración emitido:**
- **UUID:** `835be108-986d-4e98-84c3-2d0155925a4f`
- **Receptor:** CADI SOLUCIONES Y SUMINISTROS DE SISTEMAS
- **Total:** $2,900.00 MXN (IVA incluido)
- **Sellado:** TimbraCFDI / SAT sandbox

---

## Por qué es importante

### Para el cliente
Hoy, emitir una factura correcta requiere que alguien conozca:
- el régimen fiscal correcto del cliente receptor
- el código SAT del producto o servicio
- las reglas de validación del CFDI 4.0
- los plazos y formatos del SAT

Digestor Fiscal elimina esa carga. **El cliente sube su constancia y el sistema hace el resto.**

### Para el negocio
| Métrica | Proceso manual | Con Digestor Fiscal |
|---------|---------------|---------------------|
| Tiempo para emitir primera factura a cliente nuevo | 2–4 horas | \< 5 minutos |
| Errores de datos fiscales en captura | Frecuentes | Corregidos automáticamente |
| Conocimiento fiscal requerido por el operador | Alto | Mínimo |
| Escalabilidad (clientes simultáneos) | Limitada por personal | Ilimitada |

### Para la propuesta de valor
Este hito demuestra que Digestor Fiscal no es un digitalizador de documentos — es un **motor de operación fiscal**:

1. **Lectura inteligente** — Extrae datos de cualquier CSF del SAT, incluso con calidad de OCR baja
2. **Validación con IA** — Detecta y corrige errores antes de que lleguen al XML
3. **Generación automática** — Construye el CFDI 4.0 cumpliendo todas las reglas del SAT
4. **Timbrado inmediato** — Integrado con PAC certificado, listo para producción

---

## Qué está listo para el demo con prospectos

- [x] Subir CSF real y ver los datos extraídos en segundos
- [x] Ver las correcciones que hace la IA campo por campo
- [x] Seleccionar productos del catálogo y configurar la factura
- [x] Previsualizar el documento antes de emitir
- [x] Timbrar y obtener el CFDI sellado con UUID válido
- [x] Base de datos persistente — los datos del prospecto se conservan entre sesiones

---

## Próximos pasos hacia producción

1. **Certificados reales** — Sustituir RFC de prueba por CSD del emisor real
2. **PAC producción** — Activar entorno productivo en TimbraCFDI o equivalente
3. **Descarga de CFDI** — Endpoint para descargar XML + PDF de representación impresa
4. **Portal del cliente** — Vista donde el receptor puede consultar y descargar sus facturas
5. **Cancelación de CFDI** — Flujo de cancelación ante el SAT

---

*Digestor Fiscal — Del PDF al CFDI timbrado, sin fricción.*
