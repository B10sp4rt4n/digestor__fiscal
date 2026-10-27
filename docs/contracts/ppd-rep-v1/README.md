# PPD/REP v1 — contrato de diseño, NO capacidad habilitada

Estado: propuesta revisable del 26-10-2026. No agrega rutas, flags, migraciones,
workers ni imports a la aplicación. Los scripts son un modelo ejecutable aislado;
no generan XML, firmas ni CFDI. No sustituyen pruebas del builder, PostgreSQL o PAC.

## 1. Fuentes de implementación y compatibilidad

Revisión fijada, no afirmación sobre producción:

| Fuente | SHA | Contrato observado |
|---|---|---|
| Cotiza PR35 | `6a20fd80894fb1f86d364afaf4ab8fe31e81b14c` | `lib/validations/proposal-invoice.ts` exige PUE; POST invoice exige huella y clasificaciones exactas, propuesta won y tenant habilitado |
| Cotiza adaptador | mismo SHA | `lib/integrations/digestor-fiscal.ts`: HTTP server-only, credenciales de un emisor y gate DIGESTOR_FISCAL_TENANT_ID; importes actuales number |
| Cotiza persistencia | mismo SHA | `lib/db/proposal-invoices.ts`: una factura por propuesta; CAS protege draft/stamping/stamped y UUID/XML |
| Digestor PR3 | `b8fcba8c1192a21ed77110147634f85323f9a2f0` | `app/services/fiscal_rules.py`: perfil PUE/MXN/IVA16; cálculos Decimal, almacenamiento comercial Float |
| Digestor emisión | mismo SHA | `app/api/routers/commercial_v1.py`: reserva persistente antes del PAC; processing/unknown bloquean; GET stamp-result sólo lee resultado local |
| Digestor memoria | mismo SHA | `app/services/fiscal_acts.py`, `app/models/fiscal_act.py`: cadena por company+draft, secuencia, FK compuesta; append/flush sin commit propio |

Propuesta: añadir un perfil **nuevo** `income-mxn-ppd-iva16/1` y otro
`payment-mxn-rep20-iva16/1`. No relajar `national-income-mxn-pue/1` ni cambiar
PUE a PPD en documentos existentes. El discriminante será obligatorio en la API
v2; v1 conserva comportamiento, defaults y rechazos actuales. Ningún cobro
transforma el UUID ni el método del ingreso ya timbrado. Cancelar/sustituir es
otra capacidad. No convertir importes legacy a Numeric silenciosamente.

## 2. Primera capacidad y reglas fiscales verificadas

Perfil propuesto: ingreso nacional individual, emisor habilitado actual, MXN,
partidas objeto02/IVA16, sin descuentos posteriores, retenciones o egresos;
pagos MXN por transferencia 03 como primera forma admitida. Otras formas
requieren ampliar y validar catálogos. Un REP por cobro, un nodo Pago y uno o
varios documentos del mismo receptor/emisor. No agrupación mensual en v1,
no divisas, anticipos, factoraje o importaciones de REP externos sin conciliación.

Reglas oficiales consultadas (no son reglas inventadas por AUP):

| Fuente | Regla usada |
|---|---|
| S1: RMF 2026 compilada, 2.7.1.29 y 2.7.1.32, pp.142–144 | Ingreso por total; pago posterior en complemento; CFDI de pago con Total cero sin MetodoPago/FormaPago en raíz; aplicación proporcional; por pago o mensual mismo receptor; límite quinto día natural del mes siguiente |
| S2: SAT complemento pagos | Versión 2.0; detalle de pago y documento; parcialidad, saldos e impuestos |
| S3: SAT estándar Pagos20, pp.44–50 | FormaDePagoP distinta de99; MXN TipoCambioP=1; igual moneda EquivalenciaDR=1; saldo insoluto=anterior−pagado; objeto02 requiere ImpuestosDR; agregación DR→P→Totales |

S1 https://www.sat.gob.mx/minisitio/NormatividadRMFyRGCE/documentos2026/rmf/compiladas/Compilado_Primera_Modificacion_a_la-Resolucion_Miscelanea_Fiscal_para_2026.pdf

S2 https://wwwmat.sat.gob.mx/consultas/92764/comprobante-de-recepcion-de-pagos

S3 https://wwwmat.sat.gob.mx/cs/Satellite?blobcol=urldata&blobkey=id&blobtable=MungoBlobs&blobwhere=1461175070885&ssbinary=true

El portal S2 incluye referencias históricas: no usar su FAQ para asumir plazos
actuales de cancelación. S1 compila hasta la primera modificación de julio;
queda como compuerta de liberación contrastar DOF y modificaciones posteriores
vigentes, guía, matriz, catálogos y XSD completos con hashes/versiones. El visor
no permitió leer el XSD. No se acredita certificación fiscal ni validación XSD.

Mapeo del futuro builder (validar completo contra material vigente antes de emitir):

- Ingreso: Tipo I, MXN, MetodoPago PPD, FormaPago99; conservar clasificación y
  snapshot fiscal del ingreso. Rechazar PPD+03; no reinterpretar el selector.
- REP: Tipo P, Moneda XXX, SubTotal/Total0, Exportacion01; receptor con UsoCFDI
  CP01 y demás datos fiscales revisados. Concepto estándar Pago: clave84111506,
  cantidad1, unidadACT, valor/importe0, ObjetoImp01; sin impuesto en raíz.
- Complemento Pagos versión2.0: FechaPago real, FormaDePagoP03, MonedaP MXN,
  TipoCambioP1, Monto del cobro. NumOperacion es dato fiscal opcional según reglas;
  **no** exigirlo como única identidad del dinero. La identidad interna es obligatoria.
- Por documento: UUID ingreso, MonedaDR MXN, EquivalenciaDR1, NumParcialidad,
  ImpSaldoAnt, ImpPagado, ImpSaldoInsoluto, ObjetoImpDR02; impuestos DR y P,
  y Totales MXN. El adapter no debe copiar MetodoPago a DoctoRelacionado de 2.0.

El límite de emisión es una alerta computada desde FechaPago y zona fiscal,
independiente de cuándo se capturó o concilió. Un pago de octubre vence el
5 de noviembre; no usar quinto día hábil ni fecha de captura. Una recepción
atrasada no cambia FechaPago ni se oculta: estado overdue, revisión y emisión
con fecha de expedición fiscal válida, sin backdating automático.

## 3. Dos autoridades y tres saldos

Cotiza es autoridad del registro de cobranza: identidad, evidencia, conciliación
bancaria/manual, aplicación económica, permisos y autorización. Digestor es
autoridad de documentos y aplicaciones fiscales confirmadas: validación,
parcialidades, impuestos, reserva, XML, timbre y memoria fiscal. No poner un
segundo cliente PAC en Cotiza. No hay transacción distribuida entre bases.

| Proyección | Fórmula | Efecto de REP incierto |
|---|---|---|
| Saldo por cobrar | total ingreso − aplicaciones económicas vigentes | El cobro confirmado permanece aplicado |
| Saldo documentado pendiente | total ingreso − aplicaciones respaldadas por REP vigente | No disminuye hasta confirmación fiscal |
| Pago pendiente de documentar | aplicado económico − documentado fiscal | Se muestra con alerta; nunca como deuda nueva del cliente |

Ejemplo sintético: ingreso11600; cobro4640 → saldo económico6960;
si REP incierto, documentado pendiente11600, pendiente REP4640. Tras confirmar,
saldo documentado6960. Cobro6960 → económico0; sólo con REP confirmado y sin
incidencias se muestra **Liquidada / documentación completa**. Cobranza y fiscal
son badges distintos. Vendedor ve avance; permisos de tesorería/fiscal controlan
conciliación y emisión; no añadir trabajo de conciliación al ejecutivo por defecto.

## 4. Modelo aditivo propuesto (no migración ejecutable)

Todas las claves/FK de negocio incluyen tenant y emisor; tenant se obtiene de la
sesión y se mapea explícitamente a company_id del emisor autorizado actual.
No aceptar company_id arbitrario del navegador ni habilitar multientidad por
introducir estas tablas. Todas las cantidades nuevas son Numeric(20,6) para
impuestos/base y Numeric(18,2) para MXN; API usa cadenas decimales, nunca float.

| Dueño / tabla nueva | Claves, campos e invariantes |
|---|---|
| Cotiza receivables | PK tenant+id; unique tenant+emitter+income_uuid; propuesta/trato/origen, digestor_draft_id, total/currency, snapshot_hash; original inalterable |
| Cotiza receipts | PK tenant+id; idempotency_key/hash unique por tenant+emisor+acción; source/account_ref/external_transaction_id unique cuando existe; received_at con zona, recorded_at, amount, MXN, forma03, receptor, evidence_hash, reconciliation_state, revision |
| Cotiza receipt_matches | Banco/registro manual, receipt_id, evidencia, actor, decisión, timestamp; un movimiento confirmado no puede conciliar dos receipts activos; evitar guardar cuenta completa |
| Cotiza cash_applications | receipt_id, invoice_id, amount, revision, evento; unique tenant+receipt+invoice+versión activa; total aplicado≤recibido y saldo no negativo; correcciones son contrapartidas enlazadas |
| Cotiza rep_outbox/inbox | id estable, rep_intent_id, canonical_payload_hash, intento, estado, expected_revision, correlation_id; inbox unique tenant+digestor_event_id |
| Digestor rep_intents | PK company+id; unique company+origin_system+intent_id; profile, fingerprint, receipt_snapshot, status, version; todas las aplicaciones deben sumar Monto en v1 |
| Digestor rep_documents | UUID ingreso único por company+emisor, snapshot XML/hash, total, saldo fiscal, próxima parcialidad y revision; no editar ingreso timbrado |
| Digestor rep_applications | intent_id+income_uuid unique; parcialidad y saldos derivados; reserva exclusiva por ingreso mientras unresolved; unique documento+parcialidad vigente |
| Digestor rep_attempts | intent+attempt_number unique; support_id único estable por intento, fencing_token, exact_request_hash, provider_state, UUID/XML hash/acuse; nunca reutilizar con payload diferente |
| Ambos collection/fiscal_events | secuencia única por tenant+agregado; before/after, regla, actor, causa, evidencia, hash previo; append-only y FK compuesta; commit atómico con estado |

No reutilizar billing_draft para almacenar un REP como si fuera el mismo ingreso:
la relación es 1 ingreso:N aplicaciones y 1 REP:N documentos. No cambiar columnas
invoice_uuid/XML actuales por UUID/XML del complemento. La tabla FiscalAct actual
está anclada al draft y sus transiciones prohíben cambios tras evidencia timbrada:
NO ampliar sus estados para alterar un ingreso emitido. Añadir agregado REP y
diario separado con el mismo patrón; enlazar vía causalidad/UUID al acto del ingreso.

Integridad futura en PostgreSQL: FK compuestas en **cada** enlace, CHECK de importes
y moneda, unique de deduplicación, locks de facturas por UUID ordenado, CAS revision,
índice parcial de reserva activa por ingreso, trigger append-only y rol sin rewrite.
Suma de aplicaciones necesita transacción serializable/locks; un CHECK por fila
no impide dos escritores sobreaplicando simultáneamente. Idempotencia y evento se
persisten en la misma transacción. DB aislada debe probar dos conexiones antes de liberar.

## 5. Contrato HTTP propuesto, no endpoints existentes

`wire-contract.json` es JSON Schema 2020-12 para la solicitud entre servicios,
con ejemplos sintéticos. Todos los objetos son cerrados; rechazar campos fiscales
calculados enviados por cliente. Autenticación por servicio y binding emisor/tenant
se validan aparte; una firma/hash de payload no concede autorización.

| Dueño / propuesta | Entrada / respuesta |
|---|---|
| Cotiza POST /api/receipts | fecha/monto/forma/evidencia/identidad; Idempotency-Key. 201 nuevo,200 replay,409 contenido distinto o duplicado a resolver,422 inválido |
| Cotiza POST /api/receipts/:id/reconcile | expected_revision, evidencia, decisión y actor autorizado; sin evidencia no confirmar |
| Cotiza POST /api/receipts/:id/apply | expected_revision, invoice_ids/importes elegidos; servidor obtiene snapshot y saldos; CAS→409 si cambió |
| Cotiza POST /api/receipts/:id/rep-intents | revisión/confirmación autorizada; crea outbox atómico sin llamar PAC;202 con operation_id |
| Digestor POST /v2/rep-intents | contrato cerrado; calcula saldos/parcialidad/impuestos de ledger fiscal y los compara con snapshots/versiones;201/200 idempotente,409 stale/conflict,422 no soportado |
| Digestor POST /v2/rep-intents/:id/submit | autorización explícita de emisión y expected_revision;202 reservado,409 unresolved; no auto reenvío |
| Digestor GET /v2/rep-intents/:id/result | sólo estado persistido, revisión, UUID/XML hash/referencia, aplicaciones confirmadas y último event_id; no PAC |
| Digestor POST /v2/rep-intents/:id/reconcile | operador fiscal autorizado, evidencia externa revisada y token de versión; no disponible al ejecutivo; no consulta automática de proveedor en este diseño |

Error estable: `{code, operation_id, retryable, next_action, expected_revision}`.
Casos: DUPLICATE_KEY_CONFLICT, POSSIBLE_DUPLICATE_REVIEW, TENANT_SCOPE,
STALE_SNAPSHOT, OVERAPPLICATION, UNALLOCATED_CASH, PREVIOUS_REP_PENDING,
PROVIDER_OUTCOME_UNKNOWN, DOCUMENT_MISMATCH, UNSUPPORTED_PROFILE.
Timeout/5xx no es rechazo explícito. Retries de transporte de outbox usan mismo
intent/key/hash y sólo recuperan operación; no generan un segundo submit fiscal.

## 6. Transiciones deterministas

Ingreso usa el ciclo protegido actual: not_sent→processing→stamped/rejected/unknown.
Sólo UUID/XML confirmado abre cuenta por cobrar PPD; processing/unknown no la abre.
No reemplazar evidencia histórica. La creación local es idempotente por UUID.

| Agregado / acto | Precondición → estado y efecto |
|---|---|
| receipt.record | identidad nueva, monto>0, zona válida → recorded; sin tocar saldos |
| receipt.reconcile | evidencia compatible, permiso, revisión → reconciled; identidad bancaria única |
| receipt.apply | reconciled, mismo receptor/emisor/moneda, saldo suficiente → partially_applied/applied; registra cash_application y baja saldo económico |
| rep.prepare | pago totalmente aplicado, snapshot vigente; todas las aplicaciones anteriores de esos ingresos documentadas → prepared; calcula saldo anterior fiscal y siguiente parcialidad |
| rep.reserve | prepared/rejected con nueva versión válida, sin otra reserva activa → processing; confirma reserva/request hash/acto antes del efecto externo |
| rep.local_failure | demostrado antes de envío → prepared/failed_local; libera sólo reserva fiscal; conserva dinero y cobro |
| rep.reject | rechazo terminal explícito sin evidencia de timbre → rejected; libera reserva sin avanzar parcialidad/saldo; corrección versionada, no automática |
| rep.uncertain | timeout, crash, OK sin documento válido o falla al persistir respuesta → unknown o processing bloqueado; conserva reserva y versiones |
| rep.confirm | documento timbrado validado contra intento → stamped; aplica fiscalmente una vez, incrementa parcialidad, conserva evidencia y outbox en el mismo commit |
| rep.reconcile_success | processing/unknown + evidencia validada y fencing del escritor viejo → stamped; mismo efecto atómico y sin nuevo timbrado |
| rep.reconcile_nonissuance | prueba concluyente de no emisión y escritor viejo cercado → rejected; retry requiere nueva autorización; un GET local vacío o not_found eventual no basta |
| projection.catch_up | evento nuevo/mismo UUID/hash válido → aplicado una vez; inbox dedup; hueco de secuencia pausa y recupera faltantes |

La parcialidad empieza en1 por ingreso, incluso pago total diferido. Sólo avanza
al confirmar fiscalmente. Pago recibido después puede aplicarse económicamente,
pero su REP espera la cadena fiscal previa. Recibos retroactivos posteriores a un
REP ya emitido requieren revisión de secuencia: no renumerar historia; v1 bloquea
emisión fuera de orden y solicita conciliación. No mantener lock SQL durante PAC.

## 7. Aritmética e impuestos

Para cada aplicación: `0 < aplicado <= saldo_anterior`; `saldo_posterior =
saldo_anterior - aplicado`, exacto a centavos MXN. `sum(aplicaciones) <= recibo`;
v1 exige igualdad antes de preparar REP. Sobrante se conserva como no aplicado,
sin rebautizarlo anticipo ni llevar saldo negativo. Varias facturas se aplican
por decisión explícita y todas deben pertenecer al mismo receptor y emisor.

Digestor deriva base/IVA proporcionalmente desde el **snapshot del XML de ingreso**,
no precios actuales de Cotiza ni `pago / 1.16` como única verdad. Conservar por
concepto base/IVA original y asignación acumulada; calcular en Decimal alta precisión,
serializar DR/P hasta6 decimales, Totales a2; verificar tolerancias oficiales del
estándar antes de aceptar. Una última aplicación puede absorber sólo residuo
admisible, nunca inventar impuesto para cerrar centavos. Si no existe solución
dentro de tolerancias, bloquear/revisar. No compensar errores modificando Monto.

Fixture exacta: base10000, IVA1600, total11600; pago4640 genera base4000/IVA640;
pago6960 genera base6000/IVA960. Pruebas de microimportes y distribución de residuos
con builder/XSD completo son bloqueantes para implementación, no acreditadas por
el modelo aritmético de este paquete. No duplicar revenue de ingreso con CFDI P.

## 8. Identidad, errores y recuperación

- Idempotency-Key se liga a acción+tenant+emisor+hash canónico; misma clave y
  contenido devuelve mismo resultado sin otro acto; distinto contenido409.
- ID bancario fuente+cuenta interna+transacción es unique cuando existe. Igual
  monto/fecha/RFC es señal de posible duplicado, no prueba. Manual sin ID externo
  requiere evidencia y resolución explícita; no fusionar dos pagos legítimos.
- Falla Cotiza antes de outbox commit: no existe intento. Tras commit y antes
  de enviar: replay mismo ID. Digestor aceptó y se perdió respuesta: GET/replay
  recupera operación existente; jamás crear ID nuevo para destrabarla.
- PAC emitió y falló commit: processing reservado. Conciliar por support_id y
  huella; validar XML/UUID, firma/TFD, emisor, receptor, TipoP, pagos, documentos,
  parcialidades, importes e impuestos exactos. Respuesta diferente→quarantine.
- Caída al actualizar Cotiza: Digestor conserva documento y evento; inbox replay
  actualiza espejo sin recobrar ni retimbrar. Hash/UUID diferente para mismo evento
  es conflicto, no last-write-wins. Poll autorizado puede sustituir webhook.
- Reverso bancario/devolución no cancela REP. Abre disputa y bloquea liquidación
  fiscal completa; conservar cobro original y contrapartida. Cancelación/sustitución
  de REP o ingreso requiere escenario separado, evidencia SAT y autorización;
  jamás liberar UUID ni reusar parcialidad por cambiar un estado local.
- Pago aplicado aún sin REP puede corregirse mediante contrapartida antes de
  reservar. Con intento processing/unknown/stamped, congelar aplicaciones hasta
  resolución fiscal. Discrepancia de bancos no elimina dinero registrado.

## 9. AUP y memoria Recordia

AUP se usa como tabla de decisiones versionada: contexto autenticado+snapshot+
estado+regla→permitir/rechazar y razón. No interpreta libremente reglas fiscales.
Recordia conserva el acto y la evidencia, no autoriza el pago ni certifica al SAT.

Evento v1: recordia_version, contract_version, event_id, tenant_id, case_id,
subject(entity_type/id), sequence, command_id, correlation_id, causation_id,
occurred_at original con zona y UTC normalizado, recorded_at_utc, actor/rol e
identity_status, epistemic_class, support_status, source_refs, evidence_refs,
policy_or_rule_refs, state_before/after, payload_hash, previous_event_id/hash.
Cotiza registra receipt/application/outbox; Digestor registra rep/attempt/result.
No compartir una cadena física entre bases; enlazar IDs y hashes de snapshots.

Un usuario declara recepción (`recorded_claim`); la decisión de conciliar es
`decision` con evidencia; un acto observado de la aplicación es `observed_fact`
con support_scope=application_transition_only. No afirmar verificación bancaria
externa por un hash. Corrección añade `corrects`/`supersedes`, no UPDATE del pasado.
Verificador exige continuidad, secuencia, tenant y hash. Trigger y privilegios
protegen escrituras ordinarias; administrador DB puede alterarlos. Memoria local,
sin custodia externa ni sellado HotVault. Retener hashes, localizadores protegidos
y datos mínimos, no estados de cuenta completos en eventos visibles al vendedor.

## 10. Entrega, reproducción y liberación

`python3 -I scripts/ppd_rep_contract/test_reference.py` ejecuta stdlib aislada,
sin cargar app/config/.env, con sockets bloqueados. Comprueba modelo de estados
y fixtures sintéticas; no PostgreSQL, PAC, catálogo ni CFDI real. Copia idéntica
en ambos repositorios para revisión coordinada, no doble implementación productiva.

`result-contract.json` define la respuesta: estados inciertos nunca retryable y
estado stamped exige evidencia de documento. Para comprobar ambos JSON Schema:

```bash
python3 -m venv /tmp/ppd-contract-tests
/tmp/ppd-contract-tests/bin/pip install -r scripts/ppd_rep_contract/requirements-test.txt
/tmp/ppd-contract-tests/bin/python scripts/ppd_rep_contract/test_wire.py
```

Instalar dependencias es preparación; la ejecución de estas pruebas no necesita
red. Validar formatos/calendarios nuevamente en servidor: JSON Schema no prueba
autorización, suma de aplicaciones, semántica fiscal ni vigencia de evidencia.
El modelo reducido no implementa SQL, outbox real, impuestos, cancelaciones,
resolución de duplicados manuales o correcciones; especificaciones de aceptación
de esos pasos aparecen en `acceptance-matrix.md`. La memoria del modelo sólo
prueba hash/secuencia de eventos sintéticos; no es el diario productivo completo.

Orden de implementación:
1. Revisar/aceptar contrato y resolver material fiscal vigente pendiente.
2. Migraciones aditivas aisladas, Numeric, constraints, diario y pruebas PG17
   de dos conexiones, permisos, cross-tenant, crash y recuperación/backups.
3. Ledger de cobranza/outbox Cotiza con mock Digestor; API nueva y autorizaciones.
4. Agregado/builder REP Digestor con PAC simulado, XSD/catálogos versionados,
   tolerancias fiscales y validación de respuesta completa.
5. Prueba coordinada con fallos inyectados, regresión PUE y XML histórico idéntico.
6. Mantener ambos perfiles nuevos deshabilitados por defecto. Antes de cualquier
   activación: migración en mantenimiento, respaldo y recuperación demostrados,
   exclusividad de escritores/CRM y aprobación coordinada de PR35+Digestor3.
   PAC incluso sandbox requiere autorización aparte; no activación productiva aquí.

Compuertas de aceptación aún pendientes: pruebas de handlers reales, rollback de
estado+evento, concurrencia SQL real, firma/TFD, validación XSD/matriz vigente,
catálogos, cancelaciones externas, migración/backups y end-to-end. Este diseño
no hereda como propias las 562/89 pruebas reportadas en sus PR base.
