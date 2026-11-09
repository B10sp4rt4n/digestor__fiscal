# Sustitución de REP: continuidad y estado incierto

El ensayo aislado `test_rep_replacement_survives_lost_issue_cancel_and_status_without_changing_cash`
de `tests/test_fiscal_revisions.py` completa el ciclo con la lógica real y un proveedor
simulado, sin red externa. Conserva un pago de 116.00 y saldo cero durante tres
interrupciones: aceptación de la sustituta sin respuesta, cancelación sin respuesta
y primera consulta de estado fallida. Bloquea emitir/descartar/cancelar antes de
recuperar la sustituta, recupera el mismo UUID sin reemitir, evita reenviar la
cancelación y activa el nuevo XML sólo al confirmar la cancelación del anterior.
El XML original, su relación de sustitución y la declaración económica se conservan.

33 pruebas de sustituciones/cancelación aprobaron; tres PostgreSQL quedaron omitidas
sin una base loopback configurada. La evidencia opcional del caso integrado se
guarda con `REP_SUBSTITUTION_EVIDENCE=RUTA_NUEVA`.

## Ensayo público real del 9 de noviembre de 2026

`run_public_rep_substitution.py` acepta únicamente un checkpoint local del ensayo
público anterior, copia su SQLite y limita la red al PAC público sandbox. Reutilizó
el REP `8feeec54-8e9e-4e40-bda9-7e0c3c333723`, sin emitir otra factura ni otro pago.
Preparó la corrección de forma 03→02 y referencia, pero su única solicitud de
timbrado no dejó una respuesta ni UUID confirmados en el arnés. Digestor conservó
la sustitución `609bd2e0-6dde-4818-bdb3-b57195e36408` en `unknown`.

El resultado es **parcial y bloqueado**, no una sustitución concluida ni una
aceptación acreditada. El corte deliberado previsto no llegó a ejecutarse.
El arnés inicial no conservó la excepción de transporte; no se afirma un código
HTTP, timeout ni causa atribuible al PAC. No se repitió el timbrado y no se intentó
cancelar el original sin una sustituta confirmada.
Una consulta adicional, sólo de lectura, a `ConsultaCfdisRelacionadosEmisor`
respondió HTTP 200 con `codStatus=2001` y las listas `UUIDsHijo`/`UUIDsPadre`
vacías. No aportó un UUID recuperable; no se interpreta como prueba concluyente
de que el timbrado no ocurrió. El ejecutor ahora registra el tipo de excepción
de transporte en ejecuciones futuras, sin añadirlo retroactivamente a ese intento.

`verify_unknown_rep_substitution.py --evidence-dir RUTA_DEL_ENSAYO` bloquea toda
red externa y comprueba el checkpoint: emitir, descartar y cancelar devuelven 409;
el original sigue activo, un pago y saldo cero, sustitución pendiente e historial
válido. La base de origen permanece intacta. Para recuperar este caso se requiere
un UUID confirmado por el proveedor; no usar otro folio como sustituto ni liberar
la reserva por tiempo transcurrido.

Las operaciones documentadas del proveedor usan `CancelaCFDIAck` con el UUID de
origen y sustitución, y `ConsultaEstatusSat` para consultar el estado. El intento
actual no alcanzó esas llamadas. Referencia técnica revisada:
https://developers.timbracfdi33.mx/reference/timbrado_cancelacfdiack

## Interfaz Cotiza

Las regresiones del componente real (React/JSDOM, transporte simulado) mostraron
que una respuesta perdida dejaba visibles las acciones de emitir o cancelar.
La corrección conserva estado local incierto hasta leer al servidor, sin cambiar
el estado fiscal persistido. Se preservan los mensajes recibidos del backend.
También se verificó que las rutas distingan el sandbox por el contexto autenticado:
los habilitadores productivos globales no deben exigir payload de operación real
a un tenant de pruebas. La producción conserva sus confirmaciones y versión exacta.
