#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

API_HOST="127.0.0.1"
API_PORT="8000"
UI_PORT="8501"

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then
    kill "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "${UI_PID:-}" ]]; then
    kill "$UI_PID" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT

echo "[Digestor] Iniciando backend en http://${API_HOST}:${API_PORT} ..."
hypercorn app.main:app --reload --bind "${API_HOST}:${API_PORT}" &
API_PID=$!

sleep 2

echo "[Digestor] Iniciando frontend en http://localhost:${UI_PORT} ..."
streamlit run upload_ui.py --server.port "${UI_PORT}" --server.headless true &
UI_PID=$!

echo "[Digestor] Listo"
echo "Backend:  http://${API_HOST}:${API_PORT}/health"
echo "Frontend: http://localhost:${UI_PORT}"
echo "Presiona Ctrl+C para detener ambos servicios"

wait -n "$API_PID" "$UI_PID"
