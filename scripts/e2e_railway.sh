#!/usr/bin/env bash
set -euo pipefail

# Explicit opt-in: use an isolated test deployment and a synthetic fixture.
: "${API_URL:?Define API_URL con un entorno de pruebas aislado}"
: "${PDF_PATH:?Define PDF_PATH con un PDF sintetico}"

if [[ ! -f "$PDF_PATH" ]]; then
  echo "ERROR: no existe PDF_PATH=$PDF_PATH"
  exit 1
fi

echo "API_URL=$API_URL"
echo "PDF_PATH=$PDF_PATH"

USERNAME="demo_$(date +%s)"
echo "[1/6] Creando sandbox user: $USERNAME"
RESP=$(curl -s -X POST "$API_URL/auth/sandbox/signup" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$USERNAME\"}")

TOKEN=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))')
if [[ -z "$TOKEN" ]]; then
  echo "ERROR: no se pudo obtener access_token"
  echo "RESP=$RESP"
  exit 1
fi

echo "[2/6] Validando /auth/me"
curl -s "$API_URL/auth/me" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

echo "[3/6] Subiendo PDF"
UP=$(curl -s -X POST "$API_URL/v1/documents" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@$PDF_PATH" \
  -F "document_type=csf")

JOB_ID=$(echo "$UP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("job_id",""))')
if [[ -z "$JOB_ID" ]]; then
  echo "ERROR: no se obtuvo job_id"
  echo "UP=$UP"
  exit 1
fi

echo "JOB_ID=$JOB_ID"
sleep 3

echo "[4/6] Consultando job"
DOC=$(curl -s "$API_URL/v1/documents/$JOB_ID" -H "Authorization: Bearer $TOKEN")
STATUS=$(echo "$DOC" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status",""))')
DOCUMENT_ID=$(echo "$DOC" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("document_id",""))')
COMPANY_ID=$(echo "$DOC" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("company_id",""))')

echo "STATUS=$STATUS"
echo "DOCUMENT_ID=$DOCUMENT_ID"
echo "COMPANY_ID=$COMPANY_ID"

if [[ -z "$DOCUMENT_ID" ]]; then
  echo "ERROR: document_id vacío"
  echo "$DOC" | python3 -m json.tool
  exit 1
fi

echo "[5/6] Aprobando documento"
APPROVE=$(curl -s -X POST "$API_URL/v1/documents/$DOCUMENT_ID/approve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{}")
echo "$APPROVE" | python3 -m json.tool

APPROVE_ERROR=$(echo "$APPROVE" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("detail",""))')
if [[ -n "$APPROVE_ERROR" ]]; then
  echo "ERROR en approve: $APPROVE_ERROR"
  echo "Sugerencia: usa un PDF de CSF nuevo (no previamente cargado) y vuelve a ejecutar."
  exit 2
fi

echo "[6/6] Enviando outbound y consultando status"
IDEMP="idem-$(date +%s)"
EVENT_ID="evt-$(date +%s)"

OUTBOUND=$(curl -s -X POST "$API_URL/v1/sync/outbound" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEMP" \
  -d "{\"event_type\":\"document.approved\",\"event_id\":\"$EVENT_ID\",\"document\":{\"document_id\":\"$DOCUMENT_ID\"}}")
echo "$OUTBOUND" | python3 -m json.tool

OUTBOUND_ERROR=$(echo "$OUTBOUND" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("detail",""))')
if [[ -n "$OUTBOUND_ERROR" ]]; then
  echo "ERROR en outbound: $OUTBOUND_ERROR"
  exit 3
fi

STATUS_OUT=$(curl -s "$API_URL/v1/sync/outbound/$EVENT_ID" \
  -H "Authorization: Bearer $TOKEN")
echo "$STATUS_OUT" | python3 -m json.tool

STATUS_ERROR=$(echo "$STATUS_OUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("detail",""))')
if [[ -n "$STATUS_ERROR" ]]; then
  echo "ERROR en status outbound: $STATUS_ERROR"
  exit 4
fi

echo "OK: registro y consulta outbound completados; entrega externa NO verificada"
