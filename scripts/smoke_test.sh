#!/usr/bin/env bash
# Prueba de humo extremo a extremo: salud -> carga -> ingesta -> consulta RAG -> FinOps.
# Uso: ./scripts/smoke_test.sh   (variables: API_URL, SMOKE_TIMEOUT)
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
TIMEOUT="${SMOKE_TIMEOUT:-90}"
MARCA="$(date +%s)"
CID="smoke-${MARCA}"
ARCHIVO="./.smoke_${MARCA}.txt"
trap 'rm -f "$ARCHIVO"' EXIT
AUTH=()
if [ -n "${API_KEY:-}" ]; then AUTH=(-H "X-API-Key: ${API_KEY}"); fi

fallar() { echo "FALLO: $1" >&2; exit 1; }

echo "1/6 Salud del servicio..."
curl "${AUTH[@]}" -fsS "$API/health" | grep -q '"status":"ok"' || fallar "/health no reporta estado ok"

echo "2/6 Carga de documento..."
cat > "$ARCHIVO" << DOC
Manual de personal ${MARCA}.
El personal tiene derecho a quince dias habiles de vacaciones anuales.
Para consultas escribir a rrhh@empresa.cl o informar el RUT 12.345.678-5 al area de personas.
DOC
resp="$(curl "${AUTH[@]}" -sS -X POST "$API/api/v1/documentos" -H "X-Correlation-ID: $CID" \
  -F "archivo=@${ARCHIVO};filename=smoke-${MARCA}.txt;type=text/plain")"
doc_id="$(echo "$resp" | sed -n 's/.*"documento_id":"\([^"]*\)".*/\1/p')"
[ -n "$doc_id" ] || fallar "la carga no devolvió documento_id: $resp"
echo "    documento_id=$doc_id"

echo "3/6 Esperando indexación (máximo ${TIMEOUT}s)..."
inicio="$(date +%s)"
while true; do
  estado="$(curl "${AUTH[@]}" -fsS "$API/api/v1/documentos/$doc_id" | sed -n 's/.*"estado":"\([A-Z]*\)".*/\1/p')"
  case "$estado" in
    INDEXADO) break ;;
    ERROR|DLQ) fallar "el documento terminó en estado $estado" ;;
  esac
  if [ $(( $(date +%s) - inicio )) -gt "$TIMEOUT" ]; then
    fallar "tiempo agotado; estado actual: ${estado:-desconocido}. ¿Está levantado el worker?"
  fi
  sleep 2
done

echo "4/6 Consulta RAG..."
pregunta='{"pregunta":"dias de vacaciones anuales del personal","top_k":20}'
consulta="$(curl "${AUTH[@]}" -sS -X POST "$API/api/v1/rag/consultar" -H 'Content-Type: application/json' -d "$pregunta")"
echo "$consulta" | grep -q "$doc_id" || fallar "la respuesta no cita el documento cargado"

echo "5/6 Verificación de privacidad y caché..."
if echo "$consulta" | grep -qE '12\.345\.678-5|rrhh@empresa\.cl'; then
  fallar "la respuesta expone PII sin anonimizar"
fi
repetida="$(curl "${AUTH[@]}" -sS -X POST "$API/api/v1/rag/consultar" -H 'Content-Type: application/json' -d "$pregunta")"
echo "$repetida" | grep -q '"cache_hit":true' || fallar "la segunda consulta no fue atendida desde caché"

echo "6/6 Resumen FinOps..."
curl "${AUTH[@]}" -fsS "$API/api/v1/finops/resumen" | grep -q '"estado_presupuesto"' || fallar "/api/v1/finops/resumen sin estado_presupuesto"

echo "OK: prueba de humo superada."
