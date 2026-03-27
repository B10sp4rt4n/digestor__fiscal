import streamlit as st
import requests
import pandas as pd

st.set_page_config(page_title="Subir CSF", layout="wide")
st.title("📄 Centro de Carga Fiscal")

API = "http://localhost:8000"


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
    st.header("Configuración")
    company_id = st.text_input("Company ID", value="mi-empresa")
    validate_online = st.checkbox("Validar QR online contra SAT", value=False)
    st.caption("La validación online puede tardar más por red o rate limits.")

tab_upload, tab_history = st.tabs(["Cargar documento", "Historial"])

with tab_upload:
    archivo = st.file_uploader("Selecciona tu CSF (.pdf o .zip)", type=["pdf", "zip"])

    if archivo and st.button("Procesar documento", use_container_width=True):
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
                    timeout=30,
                )
            else:
                resp = requests.post(
                    f"{API}/upload/zip",
                    files={"file": (archivo.name, archivo.getvalue(), "application/zip")},
                    data=payload,
                    timeout=60,
                )

        if resp.status_code == 200:
            data = resp.json()
            st.success("Documento procesado")
            if isinstance(data, list):
                st.dataframe(pd.DataFrame(data), use_container_width=True)
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
    refresh = controls_right.button("Actualizar", use_container_width=True)

    if refresh or True:
        params = {"company_id": company_id, "limit": 100}
        if q:
            params["q"] = q
        resp = requests.get(f"{API}/csf", params=params, timeout=15)
        if resp.status_code == 200:
            payload = resp.json()
            items = payload.get("items", [])
            st.caption(f"{payload.get('total', 0)} constancia(s) registradas")
            if items:
                frame = pd.DataFrame(items)
                display_columns = [
                    "uploaded_at",
                    "source_filename",
                    "rfc",
                    "razon_social",
                    "regimen",
                    "cp",
                    "qr_valid",
                    "qr_online",
                    "version",
                ]
                st.dataframe(frame[display_columns], use_container_width=True)

                options = {
                    f"{row['rfc']} | {row['razon_social']} | {row.get('source_filename') or 'sin archivo'}": row["id"]
                    for row in items
                }
                selected_label = st.selectbox("Selecciona una constancia para revalidar QR", list(options.keys()))
                selected_id = options[selected_label]

                detail_resp = requests.get(f"{API}/csf/{selected_id}", timeout=15)
                if detail_resp.status_code == 200:
                    detail = detail_resp.json()
                    with st.expander("Detalle de constancia seleccionada", expanded=True):
                        st.info(build_csf_paraphrase(detail))
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
                        st.markdown(f"**QR text:** {detail.get('qr_text') or '—'}")
                        if detail.get("extracted_text"):
                            st.text_area("Texto extraído", detail.get("extracted_text"), height=280)

                if st.button("Revalidar QR contra SAT", use_container_width=True):
                    revalidate_resp = requests.post(
                        f"{API}/csf/{selected_id}/revalidate",
                        params={"validate_online": True},
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

st.divider()
st.caption("API: http://localhost:8000 • Historial disponible en /csf")
