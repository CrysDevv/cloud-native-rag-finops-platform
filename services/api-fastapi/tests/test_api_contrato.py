import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("OTEL_SDK_DISABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import main
from config import get_settings
from rag_engine import sintetizar


class FakeRedis:
    def __init__(self, falla: bool = False):
        self.contador = 0
        self.falla = falla

    async def incr(self, clave):
        self.contador += 1
        return self.contador

    async def expire(self, clave, segundos):
        return True

    async def ping(self):
        if self.falla:
            raise ConnectionError("redis caído")
        return True


class FakeSesion:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *args, **kwargs):
        return None


class FakeGateway:
    async def verificar_s3(self):
        return None


@pytest.fixture
def cliente():
    main.app.state.redis = FakeRedis()
    main.app.state.sessionmaker = lambda: FakeSesion()
    main.app.state.gateway = FakeGateway()
    return TestClient(main.app, raise_server_exceptions=False)


def test_health_ok(cliente):
    r = cliente.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert set(r.json()["componentes"]) == {"postgresql", "redis", "localstack"}


def test_health_degradado_si_redis_falla(cliente):
    main.app.state.redis = FakeRedis(falla=True)
    r = cliente.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "degradado"
    assert r.json()["componentes"]["redis"].startswith("error")


def test_correlation_id_generado_y_propagado(cliente):
    generado = cliente.get("/health").headers["X-Correlation-ID"]
    assert str(uuid.UUID(generado)) == generado
    assert cliente.get("/health", headers={"X-Correlation-ID": "abc-123"}).headers["X-Correlation-ID"] == "abc-123"


def test_error_404_cumple_rfc_7807(cliente):
    r = cliente.get("/ruta-inexistente", headers={"X-Correlation-ID": "traza-404"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    cuerpo = r.json()
    assert cuerpo["status"] == 404 and cuerpo["correlation_id"] == "traza-404"
    assert cuerpo["instance"] == "/ruta-inexistente"


def test_validacion_422_como_problem_json(cliente):
    r = cliente.post("/api/v1/rag/consultar", json={"pregunta": "a"})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["errores"]


def test_rate_limit_devuelve_429(cliente, monkeypatch):
    monkeypatch.setattr(get_settings(), "redis_quota_requests_per_minute", 2)
    assert cliente.get("/ruta-inexistente").status_code == 404
    assert cliente.get("/ruta-inexistente").status_code == 404
    r = cliente.get("/ruta-inexistente")
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/problem+json")
    assert int(r.headers["Retry-After"]) >= 1


def test_metricas_prometheus_expuestas(cliente):
    cliente.get("/health")
    r = cliente.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text


def test_sintetizar_devuelve_citas_verificables():
    doc = uuid.uuid4()
    fragmentos = [{
        "documento_id": doc, "nombre_archivo": "politicas.txt", "pagina": 2, "indice_fragmento": 7,
        "score": 0.0123, "contenido_anonimizado": "Introducción general. El personal tiene derecho a quince dias habiles de vacaciones anuales.",
    }]
    respuesta, citas = sintetizar("dias de vacaciones anuales", fragmentos, 5)
    assert len(citas) == 1 and "[1]" in respuesta
    assert citas[0]["documento_id"] == str(doc) and citas[0]["pagina"] == 2 and citas[0]["indice_fragmento"] == 7
    assert "vacaciones" in citas[0]["extracto"]


def test_sintetizar_sin_fragmentos_no_inventa_citas():
    respuesta, citas = sintetizar("cualquier pregunta", [], 5)
    assert citas == [] and "No se encontró información suficiente" in respuesta
