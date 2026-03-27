import os
import pandas as pd
from sqlalchemy import create_engine, text

DB_URL = os.getenv("DB_URL", "sqlite:///./local.db")
engine = create_engine(DB_URL)

import streamlit as st

st.set_page_config(page_title="Digestor Fiscal — Dashboard", layout="wide")
st.title("Digestor Fiscal — Dashboard demo")

with engine.connect() as conn:
    counts = {}
    for table in ["csf","sucursal","usuario","evento_facturacion"]:
        try:
            counts[table] = conn.execute(text(f"SELECT COUNT(1) FROM {table}")).scalar() or 0
        except Exception:
            counts[table] = 0

    st.subheader("Resumen")
    st.write(pd.DataFrame([counts]))

    st.subheader("Eventos recientes")
    try:
        df = pd.read_sql(text("""
            SELECT evt_hash, company_id, total, metodo_pago, forma_pago, timestamp
            FROM evento_facturacion ORDER BY timestamp DESC LIMIT 50
        """), conn)
        st.dataframe(df, use_container_width=True)
    except Exception as e:
        st.info("Aún no hay eventos. Corre el seed_demo.py")

st.caption("Demo — conecta tu DB_URL en .env y lanza 'streamlit run streamlit_app.py'")
