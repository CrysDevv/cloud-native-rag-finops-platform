#!/usr/bin/env bash
# Levanta la plataforma completa: API, worker, PostgreSQL, Redis, LocalStack y frontend web.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

TIMEOUT="${SETUP_TIMEOUT:-240}"
MONITOREO=0; AUTH=0; DEMO=1; RECONSTRUIR=0; DETENER=0; LIMPIAR=0

uso() {
  cat << 'AYUDA'
Uso: ./setup.sh [opciones]

  (sin opciones)   Levanta API, worker, bases, LocalStack y frontend, y carga un documento de demostración.
  --monitoreo      Añade Prometheus y OpenTelemetry Collector.
  --auth           Activa autenticación por API key (genera la clave y la guarda en .api_key).
  --sin-demo       No carga el documento de demostración.
  --reconstruir    Reconstruye las imágenes sin caché.
  --detener        Detiene los contenedores conservando los datos.
  --limpiar        Detiene los contenedores y borra volúmenes (elimina los datos).
  -h, --ayuda      Muestra esta ayuda.

Requisitos: Docker con Compose >= 2.24 y curl. Variables opcionales: FRONTEND_PORT, SETUP_TIMEOUT, API_KEY.
AYUDA
}

info()   { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
aviso()  { printf '\033[1;33m[aviso]\033[0m %s\n' "$1" >&2; }
fallar() { printf '\033[1;31m[error]\033[0m %s\n' "$1" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --monitoreo) MONITOREO=1 ;;
    --auth) AUTH=1 ;;
    --sin-demo) DEMO=0 ;;
    --reconstruir) RECONSTRUIR=1 ;;
    --detener) DETENER=1 ;;
    --limpiar) LIMPIAR=1 ;;
    -h|--ayuda) uso; exit 0 ;;
    *) uso; fallar "Opción desconocida: $arg" ;;
  esac
done

hash_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then printf '%s' "$1" | sha256sum | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1
  else fallar "Se requiere sha256sum o shasum para generar la API key."; fi
}

nueva_clave() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 24
  else LC_ALL=C tr -dc 'a-f0-9' < /dev/urandom | head -c 48 || true; fi
}

fijar_env() {
  { grep -v "^$1=" .env || true; printf '%s=%s\n' "$1" "$2"; } > .env.tmp
  mv .env.tmp .env
}

# --- Requisitos ---
info "Verificando requisitos"
command -v docker >/dev/null 2>&1 || fallar "Docker no está instalado. Instale Docker Desktop: https://docs.docker.com/get-docker/"
docker info >/dev/null 2>&1 || fallar "El demonio de Docker no responde. Inicie Docker Desktop e intente de nuevo."
docker compose version >/dev/null 2>&1 || fallar "Se requiere Docker Compose v2 (comando 'docker compose')."
command -v curl >/dev/null 2>&1 || fallar "Se requiere curl."
version="$(docker compose version --short 2>/dev/null | tr -d 'v\r' || true)"
mayor="${version%%.*}"; resto="${version#*.}"; menor="${resto%%.*}"
if [[ "$mayor" =~ ^[0-9]+$ && "$menor" =~ ^[0-9]+$ ]]; then
  if [ "$mayor" -lt 2 ] || { [ "$mayor" -eq 2 ] && [ "$menor" -lt 24 ]; }; then
    fallar "Docker Compose $version es demasiado antiguo; se requiere >= 2.24 (etiqueta !reset del worker)."
  fi
fi
for f in docker-compose.yml docker-compose.worker.yml frontend-react/package.json docker/Dockerfile.python312 docker/Dockerfile.node20; do
  [ -f "$f" ] || fallar "Falta $f. Ejecute el script desde la raíz del repositorio con todas las fases fusionadas."
done

# --- Entorno ---
if [ ! -f .env ]; then
  [ -f .env.example ] || fallar "No existe .env ni .env.example."
  cp .env.example .env
  info "Se creó .env a partir de .env.example"
fi

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.worker.yml -f docker-compose.build.yml -f docker-compose.fixes.yml --profile app)
if ! docker compose -f docker-compose.yml --profile '*' config --services 2>/dev/null | grep -qx 'frontend'; then
  COMPOSE+=(-f docker-compose.frontend.yml)
fi
if [ "$MONITOREO" = 1 ] || [ "$DETENER" = 1 ] || [ "$LIMPIAR" = 1 ]; then
  [ -f docker-compose.monitoring.yml ] && COMPOSE+=(-f docker-compose.monitoring.yml)
fi

if [ "$DETENER" = 1 ]; then
  info "Deteniendo servicios (los datos se conservan)"
  "${COMPOSE[@]}" down --remove-orphans
  exit 0
fi
if [ "$LIMPIAR" = 1 ]; then
  info "Deteniendo servicios y borrando volúmenes"
  "${COMPOSE[@]}" down -v --remove-orphans
  rm -f .api_key
  exit 0
fi

# --- Autenticación opcional ---
if [ "$AUTH" = 1 ]; then
  if grep -q '^API_KEYS_SHA256=.\+' .env && [ -f .api_key ]; then
    info "Autenticación: se reutiliza la API key existente (.api_key)"
  else
    clave="$(nueva_clave)"
    [ -n "$clave" ] || fallar "No fue posible generar la API key."
    fijar_env API_KEYS_SHA256 "$(hash_sha256 "$clave")"
    printf '%s' "$clave" > .api_key
    chmod 600 .api_key 2>/dev/null || true
    info "Autenticación: API key generada y guardada en .api_key"
  fi
  COMPOSE+=(-f docker-compose.auth.yml)
fi

# --- Construcción y arranque ---
if [ "$RECONSTRUIR" = 1 ]; then
  info "Reconstruyendo imágenes sin caché"
  "${COMPOSE[@]}" build --no-cache
fi
info "Construyendo y levantando servicios (la primera vez puede tardar varios minutos)"
"${COMPOSE[@]}" up -d --build

PUERTO="${FRONTEND_PORT:-5173}"
publicado="$("${COMPOSE[@]}" port frontend 8080 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
if [ -n "$publicado" ]; then PUERTO="${publicado##*:}"; fi
URL="http://localhost:${PUERTO}"

info "Esperando a que la plataforma esté saludable (máximo ${TIMEOUT}s)"
espera=0
while :; do
  codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$URL/health" || true)"
  [ "$codigo" = "200" ] && break
  if [ "$espera" -ge "$TIMEOUT" ]; then
    aviso "Último estado de /health: HTTP ${codigo:-000}"
    "${COMPOSE[@]}" ps || true
    "${COMPOSE[@]}" logs --tail=40 api worker frontend || true
    fallar "La plataforma no quedó saludable a tiempo. Revise los logs anteriores."
  fi
  sleep 3
  espera=$((espera + 3))
done

# --- Documento de demostración ---
if [ -z "${API_KEY:-}" ] && [ -f .api_key ]; then API_KEY="$(cat .api_key)"; fi
AUTH_H=()
if [ -n "${API_KEY:-}" ]; then AUTH_H=(-H "X-API-Key: ${API_KEY}"); fi

if [ "$DEMO" = 1 ] && [ -f docs/demo/politicas_rrhh.txt ]; then
  info "Cargando documento de demostración"
  resp="$(curl -s -w '\n%{http_code}' --max-time 60 -X POST "$URL/api/v1/documentos" \
    ${AUTH_H[@]+"${AUTH_H[@]}"} \
    -F "archivo=@docs/demo/politicas_rrhh.txt;type=text/plain" \
    -F "titulo=Politicas de personal (demo)" || true)"
  codigo="${resp##*$'\n'}"
  cuerpo="${resp%$'\n'*}"
  case "$codigo" in
    201)
      doc_id="$(printf '%s' "$cuerpo" | sed -n 's/.*"documento_id":"\([^"]*\)".*/\1/p')"
      estado=""
      for _ in $(seq 1 45); do
        estado="$(curl -s --max-time 10 ${AUTH_H[@]+"${AUTH_H[@]}"} "$URL/api/v1/documentos/$doc_id" \
          | sed -n 's/.*"estado":"\([A-Z]*\)".*/\1/p' || true)"
        case "$estado" in INDEXADO|ERROR|DLQ) break ;; esac
        sleep 2
      done
      if [ "$estado" = "INDEXADO" ]; then info "Documento de demostración indexado"
      else aviso "El documento de demostración quedó en estado '${estado:-desconocido}'. Revise la pestaña Documentos."; fi
      ;;
    409) info "El documento de demostración ya estaba cargado" ;;
    401) aviso "La API exige API key: no se cargó la demo. Defina API_KEY o use --auth." ;;
    *)   aviso "No se pudo cargar la demo (HTTP ${codigo:-000})." ;;
  esac
fi

echo
echo "============================================================"
echo " Plataforma lista"
echo "   Interfaz web : $URL"
if [ -f .api_key ]; then
  echo "   API key      : $(cat .api_key)   (pegarla en el campo X-API-Key de la interfaz)"
fi
if [ "$MONITOREO" = 1 ]; then echo "   Prometheus   : http://localhost:9090"; fi
echo " Pruebe en la pestaña Consulta: \"¿Cuántos días de vacaciones tengo?\""
echo " Prueba de humo: API_URL=$URL ./scripts/smoke_test.sh"
echo " Detener: ./setup.sh --detener     Borrar todo: ./setup.sh --limpiar"
echo "============================================================"
