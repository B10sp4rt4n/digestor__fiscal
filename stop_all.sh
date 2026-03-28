#!/usr/bin/env bash
set -euo pipefail

pkill -f "hypercorn app.main:app" || true
pkill -f "streamlit run upload_ui.py" || true

echo "[Digestor] Procesos detenidos (si estaban activos)."
