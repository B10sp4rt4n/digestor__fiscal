import pandas as pd
import requests
import streamlit as st
import fitz

from streamlit_cookies_controller import CookieController
from app.services.ingest import format_extracted_csf_text

st.set_page_config(page_title="Digestor Fiscal", layout="wide")

API = "http://localhost:8000"
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

tab_upload, tab_history, tab_dashboard = st.tabs(["Cargar documento", "Historial", "Dashboard"])

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
