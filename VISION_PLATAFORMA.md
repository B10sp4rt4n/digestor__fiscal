# Digestor Fiscal — Visión de Plataforma y Plan de Acción

> Fecha: Abril 2026 | Versión: 1.0

---

## 1. El modelo que estamos construyendo

**Fiscal Identity Network** — una red donde la identidad fiscal verificada de una empresa (extraída de su fuente primaria: la CSF del SAT) se convierte en un activo reutilizable que beneficia a toda la cadena de facturación.

> No es un portal de facturación. Es la infraestructura de confianza fiscal entre empresas.

---

## 2. Este modelo ya funciona en otros mercados — y a escala

### Identidad verificada como red

| Plataforma | Industria | Modelo análogo |
|---|---|---|
| **Stripe Identity** | Fintech global | Verifica identidad una vez → reutilizable en cualquier negocio de la red Stripe |
| **Plaid** | Open Banking EUA | Conecta cuentas bancarias verificadas → cualquier app financiera las consume sin re-verificar |
| **Truora** | KYC Latam | Verifica personas/empresas → el resultado viaja entre clientes B2B |
| **Persona** | Compliance global | Identity layer reutilizable entre plataformas que necesitan onboarding regulatorio |
| **Compliancemx / SAT ID** | México | Verificación de RFC vía e.firma — pero sin capa de red ni compartición entre proveedores |
| **Dun & Bradstreet** | B2B global | Directory de identidad empresarial verificada → lo consumen bancos, aseguradoras, cadenas de suministro |
| **Open Peppol (Europa)** | Facturación B2B | Red de identidades fiscales registradas para facturación electrónica entre países de la UE |
| **GSTIN Network (India)** | GST indio | Directorio único de contribuyentes verificados → cualquier emisor consulta datos del receptor antes de facturar |

### El caso India es el más parecido

El **GSTIN (Goods and Services Tax Identification Network)** en India resolvió exactamente este problema: antes de GSTIN, cada empresa capturaba manualmente los datos fiscales de sus contrapartes. Hoy, con solo el GSTIN (equivalente al RFC mexicano), cualquier emisor consulta nombre, dirección y estado fiscal del receptor en tiempo real antes de facturar.

**India procesa ~2 mil millones de facturas electrónicas por año sobre esta infraestructura.**

México tiene el RFC y la CSF como equivalentes — pero no tiene la capa de red que los conecte entre empresas. **Eso es exactamente lo que Digestor puede construir.**

---

## 3. Por qué México está listo para esto ahora

| Condición | Estado actual |
|---|---|
| CFDI 4.0 obliga datos del receptor | ✅ Vigente desde enero 2022 |
| SAT emite CSF como documento estándar | ✅ Disponible para todos los RFC activos |
| Infraestructura fiscal digital madura | ✅ ~9,000 millones de CFDIs emitidos anualmente |
| PYME con problemas de rechazo por datos incorrectos | ✅ Problema activo y frecuente |
| AI + OCR accesibles para extracción de documentos | ✅ Ya implementado en Digestor |
| Cultura de compartir documentos fiscales entre empresas | ✅ La CSF ya se comparte por WhatsApp, correo |
| Regulación de identidad digital en México (e.firma, SAT ID) | ✅ Base legal existente |

---

## 4. Los tres escenarios que cubre la plataforma

### Escenario A — Con CSF (flujo actual)
```
Receptor entrega su CSF
→ Digestor extrae y valida automáticamente
→ Draft listo en < 3 min
→ CSF queda archivada como evidencia SAT
```

### Escenario B — Sin CSF, datos manuales (próximo)
```
Operador ingresa RFC + Nombre + CP + Régimen
→ Validación en tiempo real contra catálogos SAT
→ Si pasa: draft creado (efímero o guardado, decisión del usuario)
→ Si falla: diagnóstico campo por campo
```

### Escenario C — Perfil fiscal verificado (plataforma)
```
Receptor crea su perfil una vez (con o sin CSF)
→ Datos verificados y publicables bajo su RFC
→ Cualquier emisor en la red busca el RFC
→ Datos jalados automáticamente a cualquier draft
→ Receptor controla qué comparte y con quién
```

---

## 5. Plan de acción por etapas

---

### ETAPA 1 — Validación sin CSF (2–3 semanas)
**Objetivo:** Ningún operador queda bloqueado por falta de CSF.

#### Backend
- [ ] `POST /v1/receptor/validate` — endpoint público (sin auth)
  - Valida RFC (formato + checksum)
  - Valida CP (5 dígitos, rango válido México)
  - Valida régimen contra catálogo SAT `c_RegimenFiscal`
  - Valida compatibilidad régimen ↔ tipo de RFC (física/moral)
  - Valida UsoCFDI compatible con régimen si se incluye
  - Responde: `{ valid: bool, fields: { rfc: ok/error, cp: ok/error, ... }, message }`
- [ ] `POST /v1/billing/drafts` — ya existe, asegurar que acepta datos manuales (sin `csf_id`)
- [ ] Modelo `ReceptorProfile` (tabla `receptor_profile`) — opcional, solo si usuario decide guardar

#### Frontend (demo tab)
- [ ] Nueva sección "Facturar sin constancia" en demo tab
- [ ] Form de 5 campos con validación en tiempo real (llamada al endpoint al salir de cada campo)
- [ ] Feedback visual por campo (✅/❌ + mensaje)
- [ ] Checkbox "Guardar perfil para uso futuro" solo si pasa validación
- [ ] Botón "Crear prefactura" → flujo idéntico al de CSF desde draft en adelante

#### Página pública
- [ ] Ruta `/validar` — sin login, HTML simple o Streamlit page
- [ ] Mismo form, mismo endpoint, sin opciones de guardar ni crear draft
- [ ] CTA: "¿Quieres validar con tu constancia oficial? Súbela aquí"

---

### ETAPA 2 — Perfil fiscal del receptor (4–6 semanas)
**Objetivo:** RFC como llave de identidad fiscal reutilizable en la red.

#### Modelo de datos
- [ ] Tabla `receptor_profile`
  - `rfc` (PK natural)
  - `nombre`
  - `cp`
  - `regimen`
  - `csf_verified` (bool — ¿viene de CSF oficial?)
  - `csf_id` (FK opcional a tabla CSF)
  - `public` (bool — ¿otros emisores pueden consultar?)
  - `created_by_company_id`
  - `last_updated_at`
  - `version` (para historial de cambios de CSF)
- [ ] Migración Alembic

#### Backend
- [ ] `GET /v1/receptor/{rfc}` — autenticado, busca en directorio
  - Si existe perfil público → devuelve datos
  - Si no existe → sugiere flujo de validación manual o subir CSF
- [ ] `GET /receptor/{rfc}` — público, solo devuelve si el perfil está marcado como público
- [ ] `POST /v1/receptor/profile` — crear/actualizar perfil desde validación manual
- [ ] `POST /v1/receptor/profile/from-csf` — crear perfil desde CSF ya procesada
- [ ] Endpoint para autollenado en draft: si RFC existe en directorio → pre-llenar campos

#### Frontend
- [ ] En demo tab: campo RFC con búsqueda automática en directorio antes de pedir CSF
- [ ] Si RFC encontrado: "Datos verificados disponibles — ¿usar estos?" → draft en 1 click
- [ ] Si RFC no encontrado: opciones A (subir CSF) o B (captura manual)

---

### ETAPA 3 — Portal del receptor (6–10 semanas)
**Objetivo:** El receptor gestiona su propia identidad fiscal, la plataforma se vuelve red bilateral.

#### Nuevo tipo de cuenta: `receptor`
- [ ] Registro simplificado con RFC como usuario
- [ ] Dashboard propio: "Mi perfil fiscal"
  - Ver qué emisores han consultado su RFC
  - Subir/actualizar CSF → perfil se actualiza automáticamente
  - Controlar visibilidad: público / solo invitados / privado
  - Ver historial de facturas recibidas (si el emisor las comparte)
- [ ] Verificación por e.firma o código SAT (opcional, nivel de confianza mayor)

#### Red bilateral
- [ ] Emisor invita a receptor por correo: "Registra tu perfil fiscal para recibir tus facturas más rápido"
- [ ] Receptor acepta → perfil verificado disponible para ese emisor
- [ ] Notificaciones: "Tu receptor actualizó su CSF — sus datos han cambiado" → alerta al emisor

#### Monetización
- [ ] Perfil basic: gratuito (datos manuales validados)
- [ ] Perfil verified: con CSF adjunta (gratuito o freemium)
- [ ] Perfil premium: historial, múltiples RFC (grupos empresariales), API access

---

### ETAPA 4 — API pública y ecosistema (10–16 semanas)
**Objetivo:** Otros sistemas consumen la red como infraestructura.

- [ ] API key pública para consulta de RFC verificados
- [ ] Webhooks: "RFC actualizado" → notificar sistemas suscritos
- [ ] SDK Python/JS para integración en ERP/CRM
- [ ] Dashboard de analytics para emisores: tasa de validación, rechazos evitados, tiempo ahorrado
- [ ] Marketplace de integraciones: Odoo, Zoho, HubSpot, Contpaq

---

## 6. Métricas de éxito por etapa

| Etapa | Métrica clave | Meta 90 días |
|---|---|---|
| 1 — Validación sin CSF | % de drafts creados sin CSF | > 30% |
| 1 — Página pública | Validaciones públicas/semana | > 200 |
| 2 — Directorio receptor | RFC únicos en directorio | > 500 |
| 2 — Autollenado | % de drafts con RFC encontrado en directorio | > 15% |
| 3 — Portal receptor | Receptores registrados activos | > 100 |
| 3 — Red bilateral | Emisores con al menos 1 receptor conectado | > 20 |

---

## 7. Posicionamiento resultante

Cuando la Etapa 3 esté completa, Digestor deja de ser una herramienta de facturación y se convierte en:

> **La red de identidad fiscal verificada para empresas en México** — donde el RFC deja de ser solo un número y se convierte en un perfil vivo, actualizable y compartible entre todas las partes de una cadena de valor.

El activo principal ya no es el software — es el directorio. Y el directorio crece solo, porque cada nueva empresa que entra beneficia a todas las que ya están.

---

*"Lo que Plaid hizo con las cuentas bancarias, Digestor puede hacerlo con los datos fiscales de las empresas mexicanas."*
