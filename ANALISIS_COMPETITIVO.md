# Digestor Fiscal — Análisis Competitivo y Valor Comercial

> Fecha: Abril 2026 | Versión: 1.0

---

## 1. Contexto de mercado

México tiene **~5.3 millones de empresas registradas ante el SAT**, de las cuales ~95% son PYME (0–250 empleados). Desde 2022, la obligatoriedad del **CFDI 4.0** introdujo el campo `RegimenFiscalReceptor` y `DomicilioFiscalReceptor`, elevando la exigencia de datos fiscales correctos del receptor — datos que antes eran opcionales y ahora causan rechazo directo del PAC si están mal.

**Factores que aumentan la presión sobre las PYME:**

| Factor | Impacto |
|---|---|
| CFDI 4.0 obligatorio desde enero 2022 | Nuevos campos del receptor obligatorios |
| Resolución Miscelánea Fiscal anual | Cambios frecuentes en catálogos SAT (c_RegimenFiscal, c_UsoCFDI) |
| Auditorías digitales del SAT en aumento | Cruce automático de CFDI emitidos/recibidos |
| Incorporación de más sectores al régimen digital | Más empresas obligadas a facturar electrónicamente |
| Buzonero SAT + buzón tributario activo | Notificaciones y requerimientos automatizados |

---

## 2. Competidores directos y su modelo

### Profact / Sicofi / Contpaq i-Factura / Aspel FACTURE / Facturama

| Dimensión | Competidores típicos | Digestor Fiscal |
|---|---|---|
| **Fuente de datos del receptor** | Captura manual o XML previo | CSF (documento oficial SAT) → extracción automática |
| **Validación de datos fiscales** | Catálogos SAT básicos (RFC format) | OCR + AI con corrección de campo y confianza por campo |
| **Probabilidad de error humano** | Alta (transcripción manual) | Muy baja (extracción desde fuente primaria) |
| **Time to invoice (desde onboarding cliente)** | 15–60 min (captura, validación, correcciones) | < 3 min (subir CSF → draft listo → timbrar) |
| **Requiere contador / admin fiscal** | Frecuentemente para validar datos | No (operativo lo maneja) |
| **Soporte CFDI 4.0 campos nuevos** | Sí, pero captura manual | Sí, autocomplete desde CSF |
| **Modelo de integración** | SaaS cerrado / desktop | API-first, integrable en cualquier sistema |
| **Trazabilidad por constancia** | No | Historial por RFC con score de calidad |
| **Corrección AI de régimen fiscal** | No | Sí (normaliza texto libre → código SAT correcto) |
| **PDF prefactura** | A veces (post-timbrado) | Sí (pre y post timbrado, desde draft) |

### Facturación embebida en CRM/ERP (Salesforce, SAP, Odoo México)

- Requieren integraciones costosas y tiempo de implementación de semanas a meses.
- No resuelven el problema de onboarding fiscal del cliente — asumen que ya tienes los datos correctos.
- Digestor puede funcionar **como capa previa** que alimenta esos sistemas.

---

## 3. Propuesta de valor diferencial

### "De la constancia al CFDI en menos de 3 minutos"

El flujo completo es:

```
Cliente entrega su CSF (PDF)
        ↓
Digestor extrae + valida + corrige todos los campos fiscales
        ↓
Draft de prefactura generado automáticamente
        ↓
Operador revisa, ajusta partidas, timbra
        ↓
CFDI timbrado + PDF entregable al cliente
```

**El operador no necesita saber nada de fiscalidad.** La inteligencia está en el pipeline.

### Reducción de rechazos del PAC

Los errores más comunes que causan rechazo CFDI40xxx:
- RFC inexistente o mal formado → Digestor valida estructura + extrae de CSF oficial
- `RegimenFiscalReceptor` incorrecto → AI normaliza texto a código SAT (ej: "RIF" → "621")
- `DomicilioFiscalReceptor` incorrecto → extrae CP directamente de la constancia
- `UsoCFDI` incompatible con régimen → reglas cruzadas en validación

**Estimado conservador:** reducción del 70–85% en rechazos por datos incorrectos del receptor.

---

## 4. Time to Value (TTV)

### Para la PYME cliente

| Evento | Tiempo estimado |
|---|---|
| Setup inicial (cuenta + emisor) | 15 min |
| Primera factura emitida | < 20 min desde acceso |
| Onboarding de nuevo cliente receptor | < 3 min (subir CSF) |
| Tiempo ahorrado por factura vs método manual | 12–45 min |

**TTV real: el primer día de uso ya genera valor medible** — no hay semanas de configuración ni curva de aprendizaje técnico.

### Para el integrador / desarrollador

- API REST documentada (FastAPI + OpenAPI)
- Autenticación JWT, multi-tenant por `company_id`
- Endpoints listos: upload CSF → preview → stamp → PDF
- TTV técnico: integración funcional en < 4 horas

---

## 5. Modelos de monetización posibles

| Modelo | Descripción | Ticket estimado |
|---|---|---|
| **Por timbrado** | Cobro por CFDI emitido exitosamente | $1.50–$4.00 MXN/CFDI |
| **SaaS mensual por empresa** | Acceso ilimitado, tier por volumen | $499–$2,499 MXN/mes |
| **API B2B** | Integración en ERP/CRM de terceros | $0.80–$2.00 MXN/llamada |
| **Freemium** | Primeras 20 facturas gratis | Upsell a plan PRO |
| **Por sucursal** | Multi-sucursal, cada una como tenant | +$199 MXN/sucursal/mes |

**El mercado directo accesible (SAM):** PYME con facturación media-alta (~500K empresas en México), dispuestas a pagar por automatización fiscal. Con penetración del 0.1% a $999 MXN/mes = **~$5M MXN/mes de MRR**.

---

## 6. Ventajas de posicionamiento ante contexto regulatorio

### Por qué 2025–2026 es el momento correcto

1. **CFDI 4.0 ya es obligatorio** y las PYME siguen teniendo errores frecuentes por la nueva estructura
2. **Resolución Miscelánea 2025** amplió los supuestos de auditoría digital cruzada
3. **RESICO (Régimen Simplificado de Confianza)** agregó ~2M de contribuyentes nuevos desde 2022 que necesitan facturar pero no tienen infraestructura
4. **Buzón tributario activo** → más empresas reciben requerimientos del SAT por CFDIs mal emitidos
5. **IA generativa mainstream** → los clientes ya esperan automatización inteligente, no solo formularios
6. **Nativa API-first** → integrable en cualquier stack sin vendor lock-in

### Barrera de entrada para competidores

- El pipeline CSF → extracción + corrección AI → CFDI es específico al SAT mexicano
- Requiere conocimiento profundo de catálogos SAT, validaciones CFDI 4.0 y lógica de regímenes
- No es replicable rápido por un competidor genérico de facturación
- La base de constancias procesadas genera un dataset propio de validación (moat de datos)

---

## 7. Riesgos y consideraciones

| Riesgo | Mitigación |
|---|---|
| SAT cambia estructura de CSF | Pipeline modular; actualizar prompt/regex de extracción |
| PAC sube precios de timbrado | Negociar volumen o cambiar PAC (arquitectura agnóstica) |
| Competidor grande copia el feature | Ventaja de datos históricos + velocidad de iteración |
| Regulación de uso de AI en fiscal | Transparencia: AI sugiere, humano aprueba y timbra |

---

## 8. Resumen ejecutivo

> **Digestor Fiscal no es un portal de facturación. Es una capa de inteligencia fiscal que convierte la Constancia de Situación Fiscal en una factura timbrada sin captura manual.**

**¿Por qué importa?**
Porque el eslabón más débil en la cadena de facturación electrónica en México es la calidad del dato del receptor — y ese problema no lo resuelve ningún portal de facturación actual.

**¿Quién lo compra?**
PYME con volumen de clientes recurrentes (servicios, distribuidoras, constructoras, despachos), donde el onboarding fiscal de cada nuevo cliente es un cuello de botella operativo real.

**¿Cuánto vale?**
Conservadoramente, una PYME que factura a 50 clientes distintos por mes, ahorrando 20 min por cliente = **~16 horas/mes** de trabajo administrativo evitado. A $120 MXN/hora = **$2,000 MXN/mes de valor generado** — más que suficiente para justificar cualquier tier de precio.
