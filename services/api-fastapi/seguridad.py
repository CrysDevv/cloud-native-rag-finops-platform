import hashlib
import hmac
import logging

from fastapi import Request

from config import get_settings
from middleware import ProblemaHTTP

logger = logging.getLogger("api.seguridad")

CABECERA_API_KEY = "X-API-Key"


def hashes_configurados() -> list[str]:
    crudo = get_settings().api_keys_sha256
    return [h.strip().lower() for h in crudo.split(",") if h.strip()]


def autenticacion_activa() -> bool:
    return bool(hashes_configurados())


async def exigir_api_key(request: Request) -> None:
    hashes = hashes_configurados()
    if not hashes:
        return
    entregada = request.headers.get(CABECERA_API_KEY, "")
    digest = hashlib.sha256(entregada.encode("utf-8")).hexdigest()
    valida = False
    for esperado in hashes:
        if hmac.compare_digest(digest, esperado):
            valida = True
    if not valida:
        logger.warning("Acceso denegado: API key ausente o inválida", extra={"extra_data": {
            "ruta": request.url.path, "cabecera_presente": bool(entregada)}})
        raise ProblemaHTTP(401, "No autenticado", "Se requiere una API key válida en la cabecera X-API-Key.",
                           headers={"WWW-Authenticate": "ApiKey"})
