import os
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
import fitz

from streamlit_cookies_controller import CookieController
from app.services.ingest import format_extracted_csf_text

st.set_page_config(page_title="Digestor Fiscal", layout="wide")

API = os.getenv("DIGESTOR_API_BASE_URL", "http://localhost:8000").rstrip("/")
_COOKIE = "digestor_token"

ctrl = CookieController()


# ─────────────────────────────────────────
#  HELPERS DE AUTENTICACIÓN
# ─────────────────────────────────────────

def _do_login(username: str, password: str) -> dict | None:
    try:
        resp = requests.post(
            f"{API}/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except requests.ConnectionError:
        return None


def _validate_token(token: str) -> dict | None:
    try:
        resp = requests.get(
            f"{API}/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def _set_session(token: str, user: dict) -> None:
    st.session_state["token"] = token
    st.session_state["username"] = user["username"]
    st.session_state["role"] = user["role"]
    st.session_state["tenant_id"] = user["tenant_id"]


def _clear_session() -> None:
    for k in ("token", "username", "role", "tenant_id"):
        st.session_state.pop(k, None)
    st.session_state.pop("detail_enriched", None)
    ctrl.remove(_COOKIE)


def _fetch_dashboard_data(company_id: str) -> dict | None:
    try:
        resp = requests.get(
            f"{API}/csf/dashboard",
            params={"company_id": company_id},
            headers=auth_headers(),
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except requests.RequestException:
        return None


def _export_local_backup(company_id: str) -> dict | None:
    try:
        resp = requests.post(
            f"{API}/admin/backups/export",
            params={"company_id": company_id},
            headers=auth_headers(),
            timeout=120,
        )
        if resp.status_code == 200:
            return resp.json()
        return {"error": resp.text, "status_code": resp.status_code}
    except requests.RequestException as exc:
        return {"error": str(exc), "status_code": 0}


def describe_csf_status(item: dict) -> str:
    status = item.get("processing_status")
    reason = item.get("status_reason")
    labels = {
        "processed": "Procesada",
        "needs_review": "Requiere revision",
        "pending_qr": "Pendiente de QR",
        "incomplete": "Registro incompleto",
        "duplicate": "Duplicada",
        "updated": "Actualizada",
    }
    base = labels.get(status, status or "Sin estado")
    if reason:
        return f"{base} ({reason})"
    return base


def describe_parser_source(item: dict) -> str:
    source = item.get("parser_source")
    labels = {
        "regex": "Regex",
        "hybrid_groq": "Hibrido + Groq",
    }
    return labels.get(source, source or "No informado")


# ─────────────────────────────────────────
#  RESTAURAR SESIÓN DESDE COOKIE
# ─────────────────────────────────────────

if "token" not in st.session_state:
    saved_token = ctrl.get(_COOKIE)
    if saved_token:
        user_data = _validate_token(saved_token)
        if user_data:
            _set_session(saved_token, user_data)
        else:
            ctrl.remove(_COOKIE)


# ─────────────────────────────────────────
#  PANTALLA DE LOGIN
# ─────────────────────────────────────────

def _login_screen() -> None:
    st.title("🔐 Acceso — Digestor Fiscal")
    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Iniciar sesión", width="stretch")

    if submitted:
        if not username or not password:
            st.error("Ingresa usuario y contraseña.")
            return
        with st.spinner("Verificando..."):
            data = _do_login(username, password)
        if data:
            _set_session(data["access_token"], data)
            # max_age en segundos — 8 horas
            ctrl.set(_COOKIE, data["access_token"], max_age=28800)
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")


if "token" not in st.session_state:
    _login_screen()
    st.stop()

if "detail_enriched" not in st.session_state:
    st.session_state["detail_enriched"] = {}

if "pdf_cache" not in st.session_state:
    st.session_state["pdf_cache"] = {}


# ─────────────────────────────────────────
#  APP PRINCIPAL (usuario autenticado)
# ─────────────────────────────────────────

st.title("📄 Centro de Carga Fiscal")


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {st.session_state['token']}"}


def build_csf_paraphrase(data: dict) -> str:
    rfc = data.get("rfc") or "no identificado"
    razon = data.get("razon_social") or "sin razón social"
    regimen = data.get("regimen") or "sin régimen identificado"
    cp = data.get("cp") or "sin código postal"
    id_cif = data.get("id_cif") or "sin idCIF"
    qr_estado = "válido" if data.get("qr_valid") else "no detectado o no válido"
    qr_online = data.get("qr_online")
    qr_online_txt = "no ejecutada"
    if qr_online is True:
        qr_online_txt = "exitosa"
    elif qr_online is False:
        qr_online_txt = "sin confirmación"

    return (
        f"La constancia corresponde a {razon} (RFC {rfc}). "
        f"Se detectó el régimen {regimen} y el código postal {cp}. "
        f"El idCIF identificado es {id_cif}. "
        f"La validación local del QR es {qr_estado} y la verificación online fue {qr_online_txt}."
    )


def build_parsed_text(data: dict) -> str:
    final_payload = data.get("corrected_json") or data.get("crm_autofill") or {}
    if final_payload:
        labels = {
            "tax_id": "RFC",
            "legal_name": "Razon social",
            "trade_name": "Nombre comercial",
            "tax_regime": "Regimen",
            "postal_code": "Codigo postal",
            "curp": "CURP",
            "cif_id": "idCIF",
            "status_padron": "Estatus padron",
            "start_operations_date": "Fecha inicio operaciones",
            "last_status_change_date": "Fecha ultimo cambio estado",
            "street_type": "Tipo de vialidad",
            "street_name": "Nombre de vialidad",
            "ext_number": "Numero exterior",
            "int_number": "Numero interior",
            "neighborhood": "Colonia",
            "locality": "Localidad",
            "municipality": "Municipio",
            "state": "Estado",
            "between_street": "Entre calle",
            "and_street": "Y calle",
            "qr_url": "QR",
            "geo_city": "Geo ciudad",
            "geo_state": "Geo estado",
            "geo_latitude": "Geo latitud",
            "geo_longitude": "Geo longitud",
        }
        ordered_keys = [
            "tax_id",
            "legal_name",
            "trade_name",
            "tax_regime",
            "postal_code",
            "curp",
            "cif_id",
            "status_padron",
            "start_operations_date",
            "last_status_change_date",
            "street_type",
            "street_name",
            "ext_number",
            "int_number",
            "neighborhood",
            "locality",
            "municipality",
            "state",
            "between_street",
            "and_street",
            "qr_url",
            "geo_city",
            "geo_state",
            "geo_latitude",
            "geo_longitude",
        ]
        lines = []
        for key in ordered_keys:
            value = final_payload.get(key)
            if value:
                lines.append(f"{labels.get(key, key)}: {value}")
        if lines:
            return "\n".join(lines)

    extracted_text = data.get("extracted_text") or data.get("extracted_text_preview") or ""
    parsed = format_extracted_csf_text(extracted_text)
    if parsed:
        return parsed
    lines = [
        f"RFC: {data.get('rfc') or 'No identificado'}",
        f"Razon social: {data.get('razon_social') or 'No identificada'}",
        f"Regimen: {data.get('regimen') or 'No identificado'}",
        f"Codigo postal: {data.get('cp') or 'No identificado'}",
        f"CURP: {data.get('curp') or 'No identificada'}",
        f"idCIF: {data.get('id_cif') or 'No identificado'}",
        f"QR: {data.get('qr_text') or 'No detectado'}",
        f"Metodo de extraccion: {describe_parser_source(data)}",
    ]
    return "\n".join(lines)


def _extract_filename_from_disposition(disposition: str | None) -> str | None:
    if not disposition:
        return None
    marker = "filename="
    idx = disposition.lower().find(marker)
    if idx == -1:
        return None
    raw = disposition[idx + len(marker):].strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        raw = raw[1:-1]
    return raw or None


def _fetch_pdf_bytes(csf_id: str) -> tuple[bytes | None, str | None, str | None]:
    try:
        resp = requests.get(
            f"{API}/csf/{csf_id}/pdf",
            headers=auth_headers(),
            timeout=60,
        )
    except requests.ReadTimeout:
        return None, None, "La descarga del PDF superó el tiempo de espera."
    except requests.RequestException as exc:
        return None, None, f"Error de red al descargar PDF: {exc}"

    if resp.status_code != 200:
        return None, None, f"No se pudo descargar PDF ({resp.status_code}): {resp.text}"

    filename = _extract_filename_from_disposition(resp.headers.get("Content-Disposition"))
    return resp.content, filename, None


def _render_pdf_preview(pdf_bytes: bytes, key_prefix: str) -> None:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        st.warning(f"No se pudo renderizar el PDF en vista previa: {exc}")
        return

    try:
        if doc.page_count == 0:
            st.info("El PDF no contiene páginas para previsualizar.")
            return

        page_number = st.number_input(
            "Página",
            min_value=1,
            max_value=doc.page_count,
            value=1,
            step=1,
            key=f"pdf_page_{key_prefix}",
        )
        page = doc.load_page(int(page_number) - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        st.image(
            pix.tobytes("png"),
            caption=f"Vista previa de página {page_number} de {doc.page_count}",
            use_container_width=True,
        )
    finally:
        doc.close()

with st.sidebar:
    st.header("Sesión")
    st.markdown(f"**Usuario:** {st.session_state['username']}")
    st.markdown(f"**Rol:** {st.session_state['role']}")
    st.markdown(f"**Tenant:** {st.session_state['tenant_id']}")
    if st.button("Cerrar sesión", width="stretch"):
        _clear_session()
        st.rerun()
    if st.session_state["role"] in {"admin", "superadmin"}:
        if st.button("Generar respaldo local", width="stretch"):
            with st.spinner("Exportando respaldo..."):
                backup_result = _export_local_backup(st.session_state["tenant_id"])
            if backup_result and not backup_result.get("error"):
                st.session_state["last_backup"] = backup_result
                st.success("Respaldo local generado")
            else:
                error_text = (backup_result or {}).get("error") or "Error desconocido"
                st.error(f"No se pudo generar el respaldo: {error_text}")
        last_backup = st.session_state.get("last_backup")
        if last_backup:
            st.caption(f"Archivo: {last_backup.get('filename')}")
            st.caption(f"Ruta: {last_backup.get('backup_path')}")
    st.divider()
    validate_online = st.checkbox("Validar QR online contra SAT", value=False)
    st.caption("La validación online puede tardar más por red o rate limits.")

# Alias para las variables que el resto del UI usa
company_id = st.session_state["tenant_id"]

tab_upload, tab_history, tab_commercial, tab_validate, tab_dashboard = st.tabs(["Cargar documento", "Historial", "Demo comercial viva", "Validar receptor", "Dashboard"])

with tab_upload:
    archivo = st.file_uploader("Selecciona tu CSF (.pdf o .zip)", type=["pdf", "zip"])

    if archivo and st.button("Procesar documento", width="stretch"):
        with st.spinner("Procesando..."):
            payload = {
                "company_id": company_id,
                "validate_online": str(validate_online).lower(),
            }
            if archivo.name.endswith(".pdf"):
                resp = requests.post(
                    f"{API}/upload/pdf",
                    files={"file": (archivo.name, archivo.getvalue(), "application/pdf")},
                    data=payload,
                    headers=auth_headers(),
                       timeout=120,
                )
            else:
                resp = requests.post(
                    f"{API}/upload/zip",
                    files={"file": (archivo.name, archivo.getvalue(), "application/zip")},
                    data=payload,
                    headers=auth_headers(),
                       timeout=120,
                )

        if resp.status_code == 200:
            data = resp.json()
            st.success("Documento procesado")
            if isinstance(data, list):
                st.dataframe(pd.DataFrame(data), width="stretch")
                with st.expander("Respuesta completa"):
                    st.json(data)
            else:
                top_left, top_right, top_extra = st.columns(3)
                top_left.metric("RFC", data.get("rfc", "—"))
                top_right.metric("CP", data.get("cp", "—"))
                top_extra.metric("QR formato", "Válido" if data.get("qr_valid") else "No detectado")
                parser_left, parser_right = st.columns([2, 3])
                parser_left.metric("Metodo de extraccion", describe_parser_source(data))
                if data.get("parser_source") == "hybrid_groq":
                    parser_right.success("Se uso fallback con Groq para completar la extraccion")
                else:
                    parser_right.info("Se resolvio con regex; Groq queda disponible como fallback")

                st.info(build_csf_paraphrase(data))

                st.markdown(f"**Razón social:** {data.get('razon_social', '—')}")
                st.markdown(f"**Régimen:** {data.get('regimen', '—')}")
                st.markdown(f"**Parser usado:** {describe_parser_source(data)}")
                st.markdown(f"**idCIF:** {data.get('id_cif', '—')}")
                st.markdown(f"**QR:** {data.get('qr_text', '—')}")
                st.markdown(f"**QR online:** {data.get('qr_online', '—')}")
                st.markdown(f"**Hash:** {data.get('csf_hash', '—')}")
                geolocation = data.get("geolocation")
                if geolocation and geolocation.get("source") != "inegi_oficial":
                    geolocation = None
                if geolocation:
                    st.markdown(
                        f"**Geolocalización:** {geolocation.get('city') or '—'}, "
                        f"{geolocation.get('state') or '—'} "
                        f"({geolocation.get('latitude')}, {geolocation.get('longitude')})"
                    )
                    st.caption(
                        f"Fuente: {geolocation.get('source') or '—'} | "
                        f"Confianza: {geolocation.get('confidence', '—')}"
                    )
                    lat = geolocation.get("latitude")
                    lon = geolocation.get("longitude")
                    if lat is not None and lon is not None:
                        st.map(pd.DataFrame([{"lat": lat, "lon": lon}]), zoom=10)
                if data.get("crm_autofill"):
                    with st.expander("Autollenado CRM", expanded=True):
                        st.json(data.get("crm_autofill"))
                if data.get("ai_field_corrections"):
                    with st.expander("Corrector IA por campo", expanded=True):
                        st.dataframe(pd.DataFrame(data.get("ai_field_corrections")), width="stretch", hide_index=True)
                if data.get("field_validation"):
                    with st.expander("Validacion campo por campo", expanded=True):
                        st.dataframe(pd.DataFrame(data.get("field_validation")), width="stretch", hide_index=True)
                if data.get("corrected_json"):
                    with st.expander("JSON corregido", expanded=True):
                        st.json(data.get("corrected_json"))
                with st.expander("Texto parseado"):
                    st.text(build_parsed_text(data))
                if data.get("extracted_text_preview"):
                    with st.expander("Texto extraído (preview)"):
                        st.text(data.get("extracted_text_preview"))
                if data.get("skipped"):
                    st.info(f"Ya existía en BD ({data.get('reason')})")
                with st.expander("Respuesta completa"):
                    st.json(data)
        else:
            st.error(f"Error {resp.status_code}: {resp.text}")

with tab_history:
    controls_left, controls_right = st.columns([3, 1])
    q = controls_left.text_input("Buscar por RFC o razón social", value="")
    refresh = controls_right.button("Actualizar", width="stretch")

    if refresh or True:
        params = {"company_id": company_id, "limit": 100}
        if q:
            params["q"] = q
        resp = requests.get(f"{API}/csf", params=params, headers=auth_headers(), timeout=15)
        if resp.status_code == 200:
            payload = resp.json()
            items = payload.get("items", [])
            st.caption(f"{payload.get('total', 0)} constancia(s) registradas")
            if items:
                frame = pd.DataFrame(items)
                frame["estado"] = frame.apply(describe_csf_status, axis=1)
                frame["parser"] = frame.apply(describe_parser_source, axis=1)
                display_columns = [
                    "uploaded_at",
                    "source_filename",
                    "parser",
                    "processing_status",
                    "estado",
                    "rfc",
                    "razon_social",
                    "regimen",
                    "cp",
                    "qr_valid",
                    "qr_online",
                    "version",
                ]
                st.dataframe(frame[display_columns], width="stretch")

                options = {
                    f"{row['rfc']} | {row['razon_social']} | {row.get('source_filename') or 'sin archivo'}": row["id"]
                    for row in items
                }
                selected_label = st.selectbox("Selecciona una constancia para revalidar QR", list(options.keys()))
                selected_id = options[selected_label]

                detail_resp = requests.get(
                    f"{API}/csf/{selected_id}",
                    params={"include_geo": "false", "include_ai_corrections": "false"},
                    headers=auth_headers(),
                    timeout=15,
                )
                if detail_resp.status_code == 200:
                    detail = detail_resp.json()
                    cached = st.session_state["detail_enriched"].get(selected_id)
                    if cached and cached.get("geolocation"):
                        cached_source = (cached.get("geolocation") or {}).get("source")
                        # Limpia caché legado si la fuente ya no es válida (opencage fue proveedor anterior).
                        if cached_source != "inegi_oficial":
                            st.session_state["detail_enriched"].pop(selected_id, None)
                            cached = None
                    if cached:
                        detail.update(cached)
                    with st.expander("Detalle de constancia seleccionada", expanded=True):
                        st.info(build_csf_paraphrase(detail))
                        st.caption(f"Estado: {describe_csf_status(detail)}")
                        st.caption(f"Metodo de extraccion: {describe_parser_source(detail)}")
                        left, right = st.columns(2)
                        left.markdown(f"**RFC:** {detail.get('rfc') or '—'}")
                        left.markdown(f"**Razón social:** {detail.get('razon_social') or '—'}")
                        left.markdown(f"**Régimen:** {detail.get('regimen') or '—'}")
                        left.markdown(f"**Parser usado:** {describe_parser_source(detail)}")
                        left.markdown(f"**CP:** {detail.get('cp') or '—'}")
                        left.markdown(f"**Archivo:** {detail.get('source_filename') or '—'}")
                        right.markdown(f"**QR válido:** {detail.get('qr_valid')}")
                        right.markdown(f"**QR online:** {detail.get('qr_online')}")
                        right.markdown(f"**idCIF:** {detail.get('id_cif') or '—'}")
                        right.markdown(f"**Uploaded at:** {detail.get('uploaded_at') or '—'}")
                        right.markdown(f"**Hash:** {detail.get('csf_hash') or '—'}")
                        right.markdown(f"**Status persistido:** {detail.get('processing_status') or '—'}")
                        st.markdown(f"**QR text:** {detail.get('qr_text') or '—'}")

                        pdf_controls_left, pdf_controls_right = st.columns([1, 2])
                        if pdf_controls_left.button("Cargar PDF", key=f"pdf_{selected_id}", width="stretch"):
                            with st.spinner("Descargando PDF desde base de datos..."):
                                pdf_bytes, pdf_filename, pdf_error = _fetch_pdf_bytes(selected_id)
                                if pdf_error:
                                    st.warning(pdf_error)
                                elif pdf_bytes:
                                    if not pdf_filename:
                                        pdf_filename = detail.get("source_filename") or f"csf_{selected_id}.pdf"
                                    st.session_state["pdf_cache"][selected_id] = {
                                        "bytes": pdf_bytes,
                                        "filename": pdf_filename,
                                    }
                                    st.success("PDF cargado")

                        cached_pdf = st.session_state["pdf_cache"].get(selected_id)
                        if cached_pdf and cached_pdf.get("bytes"):
                            pdf_bytes = cached_pdf["bytes"]
                            pdf_filename = cached_pdf.get("filename") or detail.get("source_filename") or f"csf_{selected_id}.pdf"
                            pdf_controls_right.download_button(
                                "Descargar PDF",
                                data=pdf_bytes,
                                file_name=pdf_filename,
                                mime="application/pdf",
                                key=f"download_pdf_{selected_id}",
                                width="stretch",
                            )
                            with st.expander("Vista previa PDF", expanded=False):
                                _render_pdf_preview(pdf_bytes, key_prefix=selected_id)

                        geo_col, refresh_col, ia_col = st.columns(3)
                        if geo_col.button("Cargar geolocalización", key=f"geo_{selected_id}", width="stretch"):
                            with st.spinner("Consultando geolocalización..."):
                                try:
                                    geo_resp = requests.get(
                                        f"{API}/csf/{selected_id}",
                                        params={"include_geo": "true", "include_ai_corrections": "false"},
                                        headers=auth_headers(),
                                        timeout=25,
                                    )
                                    if geo_resp.status_code == 200:
                                        enriched = st.session_state["detail_enriched"].get(selected_id, {})
                                        payload = geo_resp.json()
                                        enriched["geolocation"] = payload.get("geolocation")
                                        if payload.get("crm_autofill"):
                                            enriched["crm_autofill"] = payload.get("crm_autofill")
                                        st.session_state["detail_enriched"][selected_id] = enriched
                                        st.rerun()
                                    else:
                                        st.warning(f"No se pudo cargar geolocalización: {geo_resp.text}")
                                except requests.ReadTimeout:
                                    st.warning("La geolocalización tardó demasiado. Intenta nuevamente.")
                                except requests.RequestException as exc:
                                    st.warning(f"Error al cargar geolocalización: {exc}")

                        if refresh_col.button("Forzar refresco geo", key=f"force_geo_{selected_id}", width="stretch"):
                            with st.spinner("Forzando refresco de geolocalización..."):
                                st.session_state["detail_enriched"].pop(selected_id, None)
                                try:
                                    geo_resp = requests.get(
                                        f"{API}/csf/{selected_id}",
                                        params={"include_geo": "true", "include_ai_corrections": "false"},
                                        headers=auth_headers(),
                                        timeout=25,
                                    )
                                    if geo_resp.status_code == 200:
                                        payload = geo_resp.json()
                                        enriched = {
                                            "geolocation": payload.get("geolocation"),
                                        }
                                        if payload.get("crm_autofill"):
                                            enriched["crm_autofill"] = payload.get("crm_autofill")
                                        st.session_state["detail_enriched"][selected_id] = enriched
                                        st.rerun()
                                    else:
                                        st.warning(f"No se pudo forzar geolocalización: {geo_resp.text}")
                                except requests.ReadTimeout:
                                    st.warning("El refresco de geolocalización tardó demasiado. Intenta nuevamente.")
                                except requests.RequestException as exc:
                                    st.warning(f"Error al forzar geolocalización: {exc}")

                        if ia_col.button("Cargar corrector IA", key=f"ia_{selected_id}", width="stretch"):
                            with st.spinner("Consultando corrector IA..."):
                                try:
                                    ia_resp = requests.get(
                                        f"{API}/csf/{selected_id}",
                                        params={"include_geo": "false", "include_ai_corrections": "true"},
                                        headers=auth_headers(),
                                        timeout=120,
                                    )
                                    if ia_resp.status_code == 200:
                                        enriched = st.session_state["detail_enriched"].get(selected_id, {})
                                        payload = ia_resp.json()
                                        enriched["ai_field_corrections"] = payload.get("ai_field_corrections")
                                        enriched["corrected_json"] = payload.get("corrected_json")
                                        enriched["field_validation"] = payload.get("field_validation")
                                        if payload.get("crm_autofill"):
                                            enriched["crm_autofill"] = payload.get("crm_autofill")
                                        st.session_state["detail_enriched"][selected_id] = enriched
                                        st.rerun()
                                    else:
                                        st.warning(f"No se pudo cargar corrector IA: {ia_resp.text}")
                                except requests.ReadTimeout:
                                    st.warning("El corrector IA tardó demasiado. Intenta nuevamente.")
                                except requests.RequestException as exc:
                                    st.warning(f"Error al cargar corrector IA: {exc}")

                        geolocation = detail.get("geolocation")
                        if geolocation and geolocation.get("source") != "inegi_oficial":
                            geolocation = None
                        if geolocation:
                            st.markdown(
                                f"**Geolocalización:** {geolocation.get('city') or '—'}, "
                                f"{geolocation.get('state') or '—'} "
                                f"({geolocation.get('latitude')}, {geolocation.get('longitude')})"
                            )
                            st.caption(
                                f"Fuente: {geolocation.get('source') or '—'} | "
                                f"Confianza: {geolocation.get('confidence', '—')}"
                            )
                            lat = geolocation.get("latitude")
                            lon = geolocation.get("longitude")
                            if lat is not None and lon is not None:
                                st.map(pd.DataFrame([{"lat": lat, "lon": lon}]), zoom=10)
                        if detail.get("crm_autofill"):
                            with st.expander("Autollenado CRM", expanded=True):
                                st.json(detail.get("crm_autofill"))
                        if detail.get("ai_field_corrections"):
                            with st.expander("Corrector IA por campo", expanded=True):
                                st.dataframe(pd.DataFrame(detail.get("ai_field_corrections")), width="stretch", hide_index=True)
                        if detail.get("field_validation"):
                            with st.expander("Validacion campo por campo", expanded=True):
                                st.dataframe(pd.DataFrame(detail.get("field_validation")), width="stretch", hide_index=True)
                        if detail.get("corrected_json"):
                            with st.expander("JSON corregido", expanded=True):
                                st.json(detail.get("corrected_json"))
                        with st.expander("Texto parseado", expanded=True):
                            st.text(build_parsed_text(detail))
                        if detail.get("extracted_text"):
                            st.text_area("Texto extraído", detail.get("extracted_text"), height=280)

                if st.button("Revalidar QR contra SAT", width="stretch"):
                    revalidate_resp = requests.post(
                        f"{API}/csf/{selected_id}/revalidate",
                        params={"validate_online": True},
                        headers=auth_headers(),
                        timeout=30,
                    )
                    if revalidate_resp.status_code == 200:
                        st.success("QR revalidado")
                        st.json(revalidate_resp.json())
                        st.rerun()
                    else:
                        st.error(f"No se pudo revalidar: {revalidate_resp.text}")
            else:
                st.info("No hay constancias registradas para este company_id.")
        else:
            st.error(f"No se pudo cargar el historial: {resp.text}")

# ─────────────────────────────────────────
#  DEMO COMERCIAL VIVA
#  Pestaña autocontenida que toma la
#  información ya recabada y simula una
#  prefactura visual.  NO modifica el flujo
#  de las demás pestañas.
# ─────────────────────────────────────────

_PAYMENT_METHOD_OPTIONS = {
    "PUE": "Pago en una sola exhibición",
    "PPD": "Pago en parcialidades o diferido",
}
_PAYMENT_FORM_OPTIONS = {
    "01": "Efectivo",
    "03": "Transferencia electrónica",
    "04": "Tarjeta de crédito",
    "28": "Tarjeta de débito",
    "99": "Por definir",
}
_CFDI_USE_OPTIONS = {
    "G01": "Adquisición de mercancías",
    "G03": "Gastos en general",
    "P01": "Por definir",
    "S01": "Sin efectos fiscales",
}


_REGIMEN_TEXT_TO_CODE = {
    "general de ley personas morales": "601",
    "personas morales con fines no lucrativos": "603",
    "sueldos y salarios": "605",
    "arrendamiento": "606",
    "regimen de enajenacion o adquisicion de bienes": "607",
    "demas ingresos": "608",
    "consolidacion": "609",
    "residentes en el extranjero sin establecimiento permanente en mexico": "610",
    "ingresos por dividendos": "611",
    "personas fisicas con actividades empresariales y profesionales": "612",
    "ingresos por intereses": "614",
    "regimen de los ingresos por obtencion de premios": "615",
    "sin obligaciones fiscales": "616",
    "sociedades cooperativas de produccion": "620",
    "incorporacion fiscal": "621",
    "actividades agricolas ganaderas silvicolas y pesqueras": "622",
    "opcional para grupos de sociedades": "623",
    "coordinados": "624",
    "regimen de las actividades empresariales con ingresos a traves de plataformas tecnologicas": "625",
    "regimen simplificado de confianza": "626",
}


def _normalize_regimen_code(raw: str) -> str:
    """Extrae el código SAT numérico de un campo de régimen (acepta '601', 'Régimen General...', etc)."""
    val = (raw or "").strip()
    if not val:
        return ""
    import re
    m = re.match(r"^(\d{3})", val)
    if m:
        return m.group(1)
    normalized = re.sub(r"[^a-z ]", "", val.lower().replace("é", "e").replace("á", "a").replace("í", "i").replace("ó", "o").replace("ú", "u")).strip()
    if normalized.startswith("regimen "):
        normalized = normalized[len("regimen "):].strip()
    if normalized.startswith("de ") or normalized.startswith("del "):
        normalized = re.sub(r"^del?\s+", "", normalized)
    for text, code in _REGIMEN_TEXT_TO_CODE.items():
        if text in normalized or normalized in text:
            return code
    return val


def _money(value):
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _safe_error(resp):
    try:
        p = resp.json()
    except ValueError:
        return resp.text
    if isinstance(p, dict):
        d = p.get("detail")
        if isinstance(d, dict):
            return str(d.get("message") or d)
        if d:
            return str(d)
    return str(p)


def _suggest_cfdi_use(rfc):
    r = (rfc or "").strip().upper()
    if r in {"XAXX010101000", "XEXX010101000"}:
        return "S01"
    return "G03"


with tab_commercial:
    st.subheader("Demo comercial viva")
    st.caption(
        "Selecciona una constancia ya procesada en Historial, arma una combinación "
        "de productos y genera la prefactura visual en tiempo real."
    )

    # ── 1) Elegir CSF ya procesada ──────────────────────
    st.markdown("### 1) Selecciona la constancia del cliente")

    try:
        _demo_history = requests.get(
            f"{API}/csf",
            params={"company_id": company_id, "limit": 100},
            headers=auth_headers(),
            timeout=20,
        )
        _demo_items = _demo_history.json().get("items", []) if _demo_history.status_code == 200 else []
    except requests.RequestException:
        _demo_items = []

    _demo_options = {
        f"{r.get('rfc','?')} | {r.get('razon_social','?')} | {r.get('source_filename','—')}": r["id"]
        for r in _demo_items
    }

    _demo_label = st.selectbox(
        "Constancia normalizada",
        options=["— elige una constancia —"] + list(_demo_options.keys()),
        key="demo_csf_selector",
    )

    _demo_csf_id = _demo_options.get(_demo_label)
    _demo_detail = st.session_state.get("demo_csf_detail")

    if st.button("Cargar constancia", key="demo_load_csf", width="stretch"):
        if not _demo_csf_id:
            st.warning("Selecciona primero una constancia del listado.")
        else:
            with st.spinner("Cargando datos normalizados..."):
                try:
                    _dr = requests.get(
                        f"{API}/csf/{_demo_csf_id}",
                        params={"include_geo": "false", "include_ai_corrections": "false"},
                        headers=auth_headers(),
                        timeout=30,
                    )
                    if _dr.status_code == 200:
                        st.session_state["demo_csf_detail"] = _dr.json()
                        st.session_state["demo_csf_id"] = _demo_csf_id
                        st.session_state.pop("demo_last_draft", None)
                        st.session_state.pop("demo_preview_html", None)
                        st.success("Constancia cargada")
                        st.rerun()
                    else:
                        st.error(f"Error al cargar: {_safe_error(_dr)}")
                except requests.RequestException as exc:
                    st.error(f"Error de red: {exc}")

    _demo_detail = st.session_state.get("demo_csf_detail")

    if _demo_detail:
        corrected = _demo_detail.get("corrected_json") or _demo_detail.get("crm_autofill") or {}
        demo_rfc = (corrected.get("tax_id") or _demo_detail.get("rfc") or "").strip().upper()
        demo_razon = corrected.get("legal_name") or _demo_detail.get("razon_social") or ""
        demo_cp = corrected.get("postal_code") or _demo_detail.get("cp") or ""
        demo_regimen = _normalize_regimen_code(_demo_detail.get("regimen") or corrected.get("tax_regime") or "")

        if demo_rfc in {"XAXX010101000", "XEXX010101000"}:
            demo_razon = "PUBLICO EN GENERAL"
            demo_regimen = "616"
            demo_cp = "32690"

        m = st.columns(4)
        m[0].metric("RFC", demo_rfc or "—")
        m[1].metric("Razón social", demo_razon[:30] or "—")
        m[2].metric("CP", demo_cp or "—")
        m[3].metric("Parser", describe_parser_source(_demo_detail))

        # ── Enriquecimiento opcional ────────────────────
        enrich_left, enrich_right = st.columns(2)
        if enrich_left.button("Enriquecer con IA + geolocalización", key="demo_enrich", width="stretch"):
            with st.spinner("Enriqueciendo..."):
                try:
                    _er = requests.get(
                        f"{API}/csf/{st.session_state['demo_csf_id']}",
                        params={"include_geo": "true", "include_ai_corrections": "true"},
                        headers=auth_headers(),
                        timeout=120,
                    )
                    if _er.status_code == 200:
                        st.session_state["demo_csf_detail"] = _er.json()
                        st.success("Constancia enriquecida")
                        st.rerun()
                    else:
                        st.warning(f"No se pudo enriquecer: {_safe_error(_er)}")
                except requests.RequestException as exc:
                    st.warning(f"Error de red: {exc}")

        if enrich_right.button("Revalidar QR con SAT", key="demo_revalidate_qr", width="stretch"):
            with st.spinner("Revalidando QR..."):
                try:
                    _qr = requests.post(
                        f"{API}/csf/{st.session_state['demo_csf_id']}/revalidate",
                        params={"validate_online": True},
                        headers=auth_headers(),
                        timeout=30,
                    )
                    if _qr.status_code == 200:
                        st.success("QR revalidado")
                        st.json(_qr.json())
                    else:
                        st.error(f"No se pudo revalidar: {_safe_error(_qr)}")
                except requests.RequestException as exc:
                    st.error(f"Error de red: {exc}")

        with st.expander("Detalle normalizado / IA", expanded=False):
            if _demo_detail.get("crm_autofill"):
                st.json(_demo_detail["crm_autofill"], expanded=False)
            if _demo_detail.get("ai_field_corrections"):
                st.dataframe(pd.DataFrame(_demo_detail["ai_field_corrections"]), width="stretch", hide_index=True)
            else:
                st.caption("Sin sugerencias IA — los datos ya vienen limpios o el proveedor no está configurado.")
            if _demo_detail.get("corrected_json"):
                st.json(_demo_detail["corrected_json"], expanded=False)

        # ── 2) Productos ────────────────────────────────
        st.markdown("### 2) Arma la combinación de productos")
        try:
            _cat_resp = requests.get(
                f"{API}/v1/catalog/products",
                params={"company_id": company_id, "active_only": False},
                headers=auth_headers(),
                timeout=20,
            )
            _catalog = _cat_resp.json() if _cat_resp.status_code == 200 else []
        except requests.RequestException:
            _catalog = []

        with st.expander("Alta rápida de producto", expanded=not bool(_catalog)):
            with st.form("demo_quick_product"):
                qc = st.columns([1.2, 2.2, 1, 1])
                q_sku = qc[0].text_input("SKU", value="SERV-DEMO")
                q_name = qc[1].text_input("Nombre", value="Consultoría estratégica")
                q_price = qc[2].number_input("Precio", min_value=0.0, value=2500.0, step=100.0)
                q_tax = qc[3].selectbox("IVA", [0.16, 0.0], format_func=lambda v: "16%" if v else "0%", key="demo_q_tax")
                q_desc = st.text_input("Descripción", value="Servicio profesional recurrente")
                if st.form_submit_button("Guardar en catálogo", width="stretch"):
                    _qp = {
                        "sku": q_sku.strip(), "name": q_name.strip(), "description": q_desc.strip() or None,
                        "price": q_price, "currency": "MXN", "tax_rate": q_tax,
                        "tax_object": "02" if q_tax else "01", "sat_product_code": "80101500",
                        "unit_code": "E48", "unit_name": "Servicio", "is_active": True,
                    }
                    _qr2 = requests.post(f"{API}/v1/catalog/products", json=_qp, headers=auth_headers(), timeout=20)
                    if _qr2.status_code == 200:
                        st.success("Producto guardado")
                        st.rerun()
                    elif _qr2.status_code == 409:
                        st.info("Ese SKU ya existe.")
                    else:
                        st.error(f"Error: {_safe_error(_qr2)}")

        _line_items = []
        _line_summary = []

        if _catalog:
            _prod_opts = {
                f"{p.get('sku','?')} — {p.get('name','?')} (${float(p.get('price',0)):,.2f})": p
                for p in _catalog if p.get("is_active", True)
            }
            _sel_labels = st.multiselect("Productos", list(_prod_opts.keys()), key="demo_sel_products")
            for lbl in _sel_labels:
                prod = _prod_opts[lbl]
                st.markdown("---")
                tc = st.columns([2.4, 1, 1, 1])
                tc[0].markdown(f"**{prod.get('name')}**  \n`{prod.get('sku','?')}`")
                qty = tc[1].number_input("Cant.", min_value=1.0, value=1.0, step=1.0, key=f"demo_qty_{prod['id']}")
                price = tc[2].number_input("Precio", min_value=0.0, value=float(prod.get("price",0)), step=100.0, key=f"demo_price_{prod['id']}")
                tax = tc[3].selectbox("IVA", [0.16, 0.0], index=0 if float(prod.get("tax_rate",0.16))>0 else 1, format_func=lambda v: "16%" if v else "0%", key=f"demo_tax_{prod['id']}")
                desc = st.text_input("Descripción", value=prod.get("description") or prod.get("name",""), key=f"demo_desc_{prod['id']}")
                sub = _money(qty * price)
                iva = _money(sub * tax) if tax > 0 else 0.0
                tot = _money(sub + iva)
                st.caption(f"Subtotal ${sub:,.2f} • IVA ${iva:,.2f} • Total ${tot:,.2f}")
                _line_items.append({"product_id": prod["id"], "description": desc, "quantity": qty, "unit_price": price, "tax_rate": tax, "tax_object": "02" if tax > 0 else "01"})
                _line_summary.append({"SKU": prod.get("sku","—"), "Concepto": desc, "Cantidad": qty, "Precio": price, "Importe": sub, "IVA": iva, "Total": tot})
        else:
            st.info("No hay productos en catálogo. Crea uno con el alta rápida.")

        with st.expander("Concepto libre (opcional)", expanded=False):
            _manual = st.checkbox("Añadir concepto manual", key="demo_manual_on")
            if _manual:
                mc = st.columns([2.3, 1, 1, 1])
                m_desc = mc[0].text_input("Concepto", key="demo_m_desc")
                m_qty = mc[1].number_input("Cant.", min_value=1.0, value=1.0, step=1.0, key="demo_m_qty")
                m_price = mc[2].number_input("Precio", min_value=0.0, value=1000.0, step=100.0, key="demo_m_price")
                m_tax = mc[3].selectbox("IVA", [0.16, 0.0], format_func=lambda v: "16%" if v else "0%", key="demo_m_tax")
                if m_desc.strip():
                    ms = _money(m_qty * m_price)
                    mi = _money(ms * m_tax) if m_tax > 0 else 0.0
                    mt = _money(ms + mi)
                    _line_items.append({"description": m_desc.strip(), "quantity": m_qty, "unit_price": m_price, "tax_rate": m_tax, "tax_object": "02" if m_tax > 0 else "01", "sat_product_code": "80101500", "unit_code": "E48"})
                    _line_summary.append({"SKU": "LIBRE", "Concepto": m_desc.strip(), "Cantidad": m_qty, "Precio": m_price, "Importe": ms, "IVA": mi, "Total": mt})

        if _line_summary:
            st.dataframe(pd.DataFrame(_line_summary), width="stretch", hide_index=True)
            sc = st.columns(3)
            sc[0].metric("Subtotal", f"${_money(sum(i['Importe'] for i in _line_summary)):,.2f}")
            sc[1].metric("IVA", f"${_money(sum(i['IVA'] for i in _line_summary)):,.2f}")
            sc[2].metric("Total", f"${_money(sum(i['Total'] for i in _line_summary)):,.2f}")

        # ── 3) Datos fiscales + prefactura ──────────────
        st.markdown("### 3) Ajusta datos fiscales y genera la prefactura")

        rc = st.columns(4)
        _c_name = rc[0].text_input("Razón social receptor", value=demo_razon, key="demo_c_name")
        _c_rfc = rc[1].text_input("RFC receptor", value=demo_rfc, key="demo_c_rfc")
        _c_cp = rc[2].text_input("CP receptor", value=demo_cp, key="demo_c_cp")
        _c_reg = rc[3].text_input("Régimen receptor", value=demo_regimen, key="demo_c_reg")

        fc = st.columns(4)
        _pay_method = fc[0].selectbox("Método de pago", list(_PAYMENT_METHOD_OPTIONS.keys()), format_func=lambda c: f"{c} — {_PAYMENT_METHOD_OPTIONS[c]}", key="demo_pay_method")
        _pay_form = fc[1].selectbox("Forma de pago", list(_PAYMENT_FORM_OPTIONS.keys()), format_func=lambda c: f"{c} — {_PAYMENT_FORM_OPTIONS[c]}", key="demo_pay_form")
        _use_cfdi = fc[2].selectbox("Uso CFDI", list(_CFDI_USE_OPTIONS.keys()), format_func=lambda c: f"{c} — {_CFDI_USE_OPTIONS[c]}", key="demo_use_cfdi", index=list(_CFDI_USE_OPTIONS.keys()).index(_suggest_cfdi_use(demo_rfc)))
        _series = fc[3].text_input("Serie", value="PF", key="demo_series")

        with st.expander("Datos del emisor", expanded=False):
            st.caption("Para timbrar en sandbox se usa el RFC de pruebas del SAT (EKU9003173C9).")
            ec = st.columns(4)
            _e_rfc = ec[0].text_input("RFC emisor", value="EKU9003173C9", key="demo_e_rfc")
            _e_name = ec[1].text_input("Nombre emisor", value="ESCUELA KEMPER URGATE", key="demo_e_name")
            _e_reg = ec[2].text_input("Régimen emisor", value="601", key="demo_e_reg")
            _e_place = ec[3].text_input("Lugar expedición", value="42501", key="demo_e_place")

        _notes = st.text_area("Notas comerciales", value="Prefactura lista para revisión", key="demo_notes", height=80)

        _can = bool(_demo_detail) and bool(_line_items)
        if st.button("Generar prefactura visual", key="demo_gen_draft", width="stretch", disabled=not _can):
            _draft_body = {
                "customer_name": _c_name or None, "customer_rfc": _c_rfc or None,
                "customer_zip": _c_cp or None, "customer_regimen": _c_reg or None,
                "customer_use_cfdi": _use_cfdi or None,
                "emitter_rfc": _e_rfc or None, "emitter_name": _e_name or None,
                "emitter_regimen": _e_reg or None, "place_of_issue": _e_place or None,
                "payment_method": _pay_method or "PUE", "payment_form": _pay_form or "01",
                "series": _series or "PF", "notes": _notes or None,
                "items": _line_items,
            }
            with st.spinner("Construyendo prefactura..."):
                _dr2 = requests.post(f"{API}/v1/billing/drafts", json=_draft_body, headers=auth_headers(), timeout=45)
            if _dr2.status_code == 200:
                _draft = _dr2.json()
                st.session_state["demo_last_draft"] = _draft
                _prev = requests.get(f"{API}/v1/billing/drafts/{_draft['id']}/preview", params={"company_id": company_id}, headers=auth_headers(), timeout=20)
                st.session_state["demo_preview_html"] = _prev.text if _prev.status_code == 200 else None
                st.success("Prefactura lista")
            else:
                st.error(f"Error: {_safe_error(_dr2)}")

        if not _can:
            st.info("Necesitas una constancia seleccionada y al menos un producto/concepto.")

        # ── 4) Vista previa + timbrado ──────────────────
        _last_draft = st.session_state.get("demo_last_draft")
        _preview_html = st.session_state.get("demo_preview_html")
        if _last_draft:
            st.markdown("### 4) Vista previa lista para presentar")
            dc = st.columns(4)
            dc[0].metric("Draft ID", _last_draft.get("id","—")[:8])
            dc[1].metric("Estatus", _last_draft.get("status","—"))
            dc[2].metric("Timbrable", "Sí" if _last_draft.get("ready_to_stamp") else "No")
            dc[3].metric("Total", f"${float(_last_draft.get('total',0)):,.2f}")
            if _preview_html:
                components.html(_preview_html, height=900, scrolling=True)
            # Botón descargar PDF
            _pdf_btn_cols = st.columns([1, 1, 2])
            with _pdf_btn_cols[0]:
                _pdf_resp = requests.get(
                    f"{API}/v1/billing/drafts/{_last_draft['id']}/pdf",
                    params={"company_id": company_id},
                    headers=auth_headers(), timeout=60,
                )
                if _pdf_resp.status_code == 200:
                    st.download_button(
                        label="Descargar PDF",
                        data=_pdf_resp.content,
                        file_name=f"prefactura_{_last_draft.get('series','PF')}-{_last_draft.get('folio', _last_draft['id'][:8])}.pdf",
                        mime="application/pdf",
                        key="demo_download_pdf",
                    )
                else:
                    try:
                        _err_detail = _pdf_resp.json().get("detail", _pdf_resp.text[:80])
                    except Exception:
                        _err_detail = _pdf_resp.text[:80]
                    st.caption(f"PDF no disponible ({_pdf_resp.status_code}: {_err_detail})")
            if _last_draft.get("status") != "stamped":
                if st.button("Timbrar en sandbox", key="demo_stamp", width="stretch"):
                    with st.spinner("Timbrando..."):
                        _sr = requests.post(
                            f"{API}/v1/billing/drafts/{_last_draft['id']}/stamp",
                            params={"company_id": company_id}, json={},
                            headers=auth_headers(), timeout=120,
                        )
                    if _sr.status_code == 200:
                        sp = _sr.json()
                        st.session_state["demo_last_draft"] = sp.get("draft")
                        st.success("Timbrada en sandbox")
                        st.json(sp.get("provider_response"), expanded=False)
                    else:
                        st.error(f"Error: {_safe_error(_sr)}")

    else:
        st.info("Elige una constancia ya procesada del listado para comenzar la simulación.")

with tab_validate:
    st.subheader("Validar datos fiscales de receptor")
    st.caption(
        "Ingresa los datos del receptor para verificar si son correctos para CFDI 4.0 "
        "antes de intentar facturar. No se guarda ningún dato."
    )

    _REGIMENES_OPCIONES = {
        "601": "601 — General de Ley Personas Morales",
        "603": "603 — Personas Morales con Fines no Lucrativos",
        "605": "605 — Sueldos y Salarios",
        "606": "606 — Arrendamiento",
        "607": "607 — Enajenación o Adquisición de Bienes",
        "608": "608 — Demás ingresos",
        "610": "610 — Residentes en el Extranjero",
        "611": "611 — Ingresos por Dividendos",
        "612": "612 — Actividades Empresariales y Profesionales",
        "614": "614 — Ingresos por intereses",
        "616": "616 — Sin obligaciones fiscales",
        "621": "621 — Incorporación Fiscal",
        "622": "622 — Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras",
        "625": "625 — Plataformas Tecnológicas",
        "626": "626 — Régimen Simplificado de Confianza (RESICO)",
    }
    _USOS_CFDI_OPCIONES = {
        "G01": "G01 — Adquisición de mercancias",
        "G02": "G02 — Devoluciones, descuentos o bonificaciones",
        "G03": "G03 — Gastos en general",
        "I01": "I01 — Construcciones",
        "I04": "I04 — Equipo de computo y accesorios",
        "D01": "D01 — Honorarios médicos y gastos hospitalarios",
        "S01": "S01 — Sin efectos fiscales",
        "CP01": "CP01 — Pagos",
        "CN01": "CN01 — Nómina",
    }

    with st.form("form_validar_receptor"):
        _vcol1, _vcol2 = st.columns(2)
        with _vcol1:
            _v_rfc = st.text_input("RFC *", placeholder="LOEM890201JN4").strip().upper()
            _v_nombre = st.text_input("Razón Social / Nombre", placeholder="Opcional")
            _v_cp = st.text_input("Código Postal (domicilio fiscal) *", placeholder="32690").strip()
        with _vcol2:
            _v_regimen = st.selectbox(
                "Régimen Fiscal *",
                options=[""] + list(_REGIMENES_OPCIONES.keys()),
                format_func=lambda x: _REGIMENES_OPCIONES.get(x, "— Selecciona —") if x else "— Selecciona —",
            )
            _v_uso = st.selectbox(
                "Uso CFDI (opcional)",
                options=[""] + list(_USOS_CFDI_OPCIONES.keys()),
                format_func=lambda x: _USOS_CFDI_OPCIONES.get(x, "— No validar —") if x else "— No validar —",
            )
            _v_pac = st.checkbox(
                "Verificar RFC contra padrón SAT (vía PAC sandbox)",
                value=False,
                help="Envía un CFDI de prueba al sandbox del PAC para confirmar que el RFC existe y está activo. Tarda ~5 seg.",
            )
        _v_submit = st.form_submit_button("Validar datos fiscales", use_container_width=True)

    if _v_submit:
        if not _v_rfc or not _v_cp or not _v_regimen:
            st.warning("RFC, Código Postal y Régimen Fiscal son obligatorios.")
        else:
            _payload = {"rfc": _v_rfc, "cp": _v_cp, "regimen": _v_regimen}
            if _v_nombre:
                _payload["nombre"] = _v_nombre
            if _v_uso:
                _payload["uso_cfdi"] = _v_uso
            _spinner_msg = "Validando contra catálogos SAT..." if not _v_pac else "Validando y verificando RFC en padrón SAT vía PAC..."
            with st.spinner(_spinner_msg):
                _vr = requests.post(
                    f"{API}/v1/receptor/validate",
                    json=_payload,
                    params={"pac_check": "true"} if _v_pac else {},
                    timeout=30,
                )
            if _vr.status_code == 200:
                _vdata = _vr.json()
                _pac = _vdata.get("pac")
                _pac_inconcluyente = _v_pac and _pac and _pac.get("rfc_active") is None

                # Banner principal — si el PAC fue inconcluyente, no mostrar verde puro
                if _vdata["valid"] and not _pac_inconcluyente:
                    st.success(f"✅ {_vdata['summary']}")
                elif _vdata["valid"] and _pac_inconcluyente:
                    st.warning(f"⚠️ Datos de formato válidos — verificación en padrón SAT no concluyente")
                else:
                    st.error(f"❌ {_vdata['summary']}")

                st.markdown("**Nivel 1 — Catálogos SAT:**")
                for _fname, _fres in _vdata["fields"].items():
                    _ico = "✅" if _fres["valid"] else "❌"
                    st.markdown(f"- {_ico} **{_fname}**: {_fres['message']}")

                # Resultado PAC (nivel 2)
                if _pac:
                    st.markdown("**Nivel 2 — Verificación padrón SAT (PAC sandbox):**")
                    if not _pac["available"]:
                        st.warning(f"⚠️ PAC no disponible: {_pac['message']}")
                    elif _pac["rfc_active"] is True:
                        st.success("✅ RFC confirmado activo en el padrón del SAT.")
                    elif _pac["rfc_active"] is False:
                        _code = _pac.get("error_code") or ""
                        st.error(f"❌ RFC no activo en padrón SAT ({_code}): {_pac['message']}")
                    else:
                        # Inconcluyente — error del emisor u otro
                        _code = _pac.get("error_code") or ""
                        _msg = _pac.get("message") or ""
                        st.warning(
                            f"⚠️ No fue posible verificar el RFC en el padrón SAT. "
                            f"El PAC reportó un error del **emisor** (no del receptor), "
                            f"por lo que los datos del receptor podrían ser correctos. "
                            f"Sube la CSF para confirmación oficial."
                            + (f"\n\nCódigo PAC: `{_code}`" if _code else "")
                        )

                if _vdata["valid"] and not _pac_inconcluyente:
                    st.info(
                        "Los datos son válidos. Puedes usarlos para crear una prefactura "
                        "en la tab **Demo comercial viva** o subir la Constancia de Situación Fiscal "
                        "para validación oficial del SAT."
                    )
            else:
                st.error(f"Error en validación: {_vr.status_code}")

with tab_dashboard:
    st.subheader("Resumen de constancias")
    dashboard = _fetch_dashboard_data(company_id)

    if not dashboard:
        st.warning("No se pudo cargar el dashboard desde la API.")
    else:
        metric_total, metric_valid, metric_invalid, metric_pending = st.columns(4)
        metric_total.metric("CSF registradas", dashboard.get("total_csf", 0))
        metric_valid.metric("QR válido", dashboard.get("qr_valid_count", 0))
        metric_invalid.metric("QR no válido", dashboard.get("qr_invalid_count", 0))
        metric_pending.metric("QR pendiente", dashboard.get("qr_pending_count", 0))

        st.caption(
            f"Archivos con origen identificado: {dashboard.get('with_source_file_count', 0)}"
        )

        left, right = st.columns([2, 1])

        recent_uploads = dashboard.get("recent_uploads", [])
        if recent_uploads:
            recent_frame = pd.DataFrame(recent_uploads)
            recent_frame["estado"] = recent_frame.apply(describe_csf_status, axis=1)
            recent_columns = [
                "uploaded_at",
                "source_filename",
                "processing_status",
                "estado",
                "rfc",
                "razon_social",
                "regimen",
                "qr_valid",
                "qr_online",
            ]
            left.subheader("Últimas constancias cargadas")
            left.dataframe(recent_frame[recent_columns], width="stretch")
        else:
            left.info("Todavía no hay constancias cargadas para este tenant.")

        regimen_breakdown = dashboard.get("regimen_breakdown", [])
        if regimen_breakdown:
            regimen_frame = pd.DataFrame(regimen_breakdown)
            right.subheader("Distribución por régimen")
            right.dataframe(regimen_frame, width="stretch", hide_index=True)
        else:
            right.info("Aún no hay régimen identificado en las constancias cargadas.")

        status_breakdown = dashboard.get("status_breakdown", [])
        if status_breakdown:
            st.subheader("Distribución por estado")
            status_frame = pd.DataFrame(status_breakdown)
            st.dataframe(status_frame, width="stretch", hide_index=True)

        st.info(
            "Este dashboard resume únicamente constancias de situación fiscal procesadas por la app. "
            "No mezcla eventos CFDI ni datos demo de facturación."
        )

st.divider()
st.caption(f"API: {API} • Historial disponible en /csf")
