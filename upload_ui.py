import pandas as pd
import requests
import streamlit as st

from streamlit_cookies_controller import CookieController

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

with st.sidebar:
    st.header("Sesión")
    st.markdown(f"**Usuario:** {st.session_state['username']}")
    st.markdown(f"**Rol:** {st.session_state['role']}")
    st.markdown(f"**Tenant:** {st.session_state['tenant_id']}")
    if st.button("Cerrar sesión", width="stretch"):
        _clear_session()
        st.rerun()
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
                    timeout=30,
                )
            else:
                resp = requests.post(
                    f"{API}/upload/zip",
                    files={"file": (archivo.name, archivo.getvalue(), "application/zip")},
                    data=payload,
                    headers=auth_headers(),
                    timeout=60,
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

                st.info(build_csf_paraphrase(data))

                st.markdown(f"**Razón social:** {data.get('razon_social', '—')}")
                st.markdown(f"**Régimen:** {data.get('regimen', '—')}")
                st.markdown(f"**idCIF:** {data.get('id_cif', '—')}")
                st.markdown(f"**QR:** {data.get('qr_text', '—')}")
                st.markdown(f"**QR online:** {data.get('qr_online', '—')}")
                st.markdown(f"**Hash:** {data.get('csf_hash', '—')}")
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
                display_columns = [
                    "uploaded_at",
                    "source_filename",
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

                detail_resp = requests.get(f"{API}/csf/{selected_id}", headers=auth_headers(), timeout=15)
                if detail_resp.status_code == 200:
                    detail = detail_resp.json()
                    with st.expander("Detalle de constancia seleccionada", expanded=True):
                        st.info(build_csf_paraphrase(detail))
                        st.caption(f"Estado: {describe_csf_status(detail)}")
                        left, right = st.columns(2)
                        left.markdown(f"**RFC:** {detail.get('rfc') or '—'}")
                        left.markdown(f"**Razón social:** {detail.get('razon_social') or '—'}")
                        left.markdown(f"**Régimen:** {detail.get('regimen') or '—'}")
                        left.markdown(f"**CP:** {detail.get('cp') or '—'}")
                        left.markdown(f"**Archivo:** {detail.get('source_filename') or '—'}")
                        right.markdown(f"**QR válido:** {detail.get('qr_valid')}")
                        right.markdown(f"**QR online:** {detail.get('qr_online')}")
                        right.markdown(f"**idCIF:** {detail.get('id_cif') or '—'}")
                        right.markdown(f"**Uploaded at:** {detail.get('uploaded_at') or '—'}")
                        right.markdown(f"**Hash:** {detail.get('csf_hash') or '—'}")
                        right.markdown(f"**Status persistido:** {detail.get('processing_status') or '—'}")
                        st.markdown(f"**QR text:** {detail.get('qr_text') or '—'}")
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
