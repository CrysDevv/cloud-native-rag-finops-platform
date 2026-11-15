import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_settings

logger = logging.getLogger("api")
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="-")

HTTP_REQUESTS = Counter("http_requests_total", "Solicitudes HTTP", ["metodo", "ruta", "estado"])
HTTP_LATENCIA = Histogram("http_request_duration_seconds", "Latencia HTTP", ["metodo", "ruta"])
RATE_LIMITED = Counter("http_rate_limited_total", "Solicitudes rechazadas por límite de tasa")

RUTAS_EXENTAS = {"/health", "/metrics"}


class ProblemaHTTP(Exception):
    def __init__(self, status: int, titulo: str, detalle: str, extra: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None):
        self.status = status
        self.titulo = titulo
        self.detalle = detalle
        self.extra = extra or {}
        self.headers = headers or {}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "nivel": record.levelname,
            "logger": record.name,
            "mensaje": record.getMessage(),
            "correlation_id": correlation_id_ctx.get(),
        }
        extra = getattr(record, "extra_data", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["excepcion"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configurar_logging(nivel: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    raiz = logging.getLogger()
    raiz.handlers = [handler]
    raiz.setLevel(nivel.upper())
    for nombre in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(nombre)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def respuesta_problema(path: str, status: int, titulo: str, detalle: str,
                       extra: dict[str, Any] | None = None,
                       headers: dict[str, str] | None = None) -> JSONResponse:
    settings = get_settings()
    cid = correlation_id_ctx.get()
    cuerpo: dict[str, Any] = {
        "type": "about:blank",
        "title": titulo,
        "status": status,
        "detail": detalle,
        "instance": path,
        "correlation_id": cid,
    }
    if extra:
        cuerpo.update(extra)
    cabeceras = {settings.correlation_id_header: cid, **(headers or {})}
    return JSONResponse(cuerpo, status_code=status, media_type="application/problem+json", headers=cabeceras)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        entrante = request.headers.get(settings.correlation_id_header, "").strip()
        cid = entrante[:128] if entrante and entrante.replace("-", "").replace("_", "").isalnum() else str(uuid.uuid4())
        token = correlation_id_ctx.set(cid)
        request.state.correlation_id = cid
        inicio = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("Error no controlado en la solicitud")
                response = respuesta_problema(request.url.path, 500, "Error interno del servidor",
                                              "Se produjo un error inesperado al procesar la solicitud.")
            duracion = time.perf_counter() - inicio
            response.headers[settings.correlation_id_header] = cid
            ruta_obj = request.scope.get("route")
            ruta = getattr(ruta_obj, "path", None) or "desconocida"
            HTTP_REQUESTS.labels(request.method, ruta, str(response.status_code)).inc()
            HTTP_LATENCIA.labels(request.method, ruta).observe(duracion)
            if request.url.path not in RUTAS_EXENTAS:
                logger.info("solicitud_http", extra={"extra_data": {
                    "metodo": request.method, "ruta": request.url.path,
                    "estado": response.status_code, "latencia_ms": round(duracion * 1000, 2)}})
            return response
        finally:
            correlation_id_ctx.reset(token)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in RUTAS_EXENTAS:
            return await call_next(request)
        settings = get_settings()
        limite = settings.redis_quota_requests_per_minute
        ip = request.client.host if request.client else "desconocido"
        ahora = time.time()
        clave = f"ratelimit:{ip}:{int(ahora // 60)}"
        try:
            redis = request.app.state.redis
            actual = await redis.incr(clave)
            if actual == 1:
                await redis.expire(clave, 90)
        except Exception:
            logger.warning("Redis no disponible para rate limiting; se permite la solicitud")
            return await call_next(request)
        restante = max(limite - actual, 0)
        if actual > limite:
            RATE_LIMITED.inc()
            espera = max(60 - int(ahora % 60), 1)
            return respuesta_problema(
                request.url.path, 429, "Demasiadas solicitudes",
                f"Se superó el límite de {limite} solicitudes por minuto.",
                headers={"Retry-After": str(espera), "X-RateLimit-Limit": str(limite), "X-RateLimit-Remaining": "0"},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limite)
        response.headers["X-RateLimit-Remaining"] = str(restante)
        return response


def registrar_middlewares(app: FastAPI) -> None:
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    @app.exception_handler(ProblemaHTTP)
    async def _problema(request: Request, exc: ProblemaHTTP):
        return respuesta_problema(request.url.path, exc.status, exc.titulo, exc.detalle, exc.extra, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return respuesta_problema(request.url.path, exc.status_code, "Error HTTP", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, exc: RequestValidationError):
        errores = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return respuesta_problema(request.url.path, 422, "Solicitud inválida",
                                  "Los datos enviados no cumplen el contrato esperado.", {"errores": errores})

    @app.exception_handler(Exception)
    async def _generico(request: Request, exc: Exception):
        logger.exception("Excepción global no controlada")
        return respuesta_problema(request.url.path, 500, "Error interno del servidor",
                                  "Se produjo un error inesperado al procesar la solicitud.")
