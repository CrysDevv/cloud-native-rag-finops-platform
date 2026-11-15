import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("OTEL_SDK_DISABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import main
from config import get_settings
from test_api_contrato import FakeGateway, FakeRedis, FakeSesion

CLAVE = "clave-de-prueba-123"
HASH = hashlib.sha256(CLAVE.encode()).hexdigest()


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_keys_sha256", HASH)
    main.app.state.redis = FakeRedis()
    main.app.state.sessionmaker = lambda: FakeSesion()
    main.app.state.gateway = FakeGateway()
    return TestClient(main.app, raise_server_exceptions=False)


def test_sin_clave_devuelve_401_problem_json(cliente):
    r = cliente.get("/api/v1/documentos")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.headers["WWW-Authenticate"] == "ApiKey"
    assert r.json()["status"] == 401


def test_clave_incorrecta_devuelve_401(cliente):
    assert cliente.get("/api/v1/documentos", headers={"X-API-Key": "otra"}).status_code == 401


def test_rag_y_finops_tambien_estan_protegidos(cliente):
    assert cliente.post("/api/v1/rag/consultar", json={"pregunta": "vacaciones anuales"}).status_code == 401
    assert cliente.get("/api/v1/finops/resumen").status_code == 401


def test_clave_valida_supera_la_autenticacion(cliente):
    r = cliente.get("/api/v1/documentos", headers={"X-API-Key": CLAVE})
    assert r.status_code != 401


def test_health_y_metrics_siguen_abiertos(cliente):
    assert cliente.get("/health").status_code == 200
    assert cliente.get("/metrics").status_code == 200


def test_autenticacion_desactivada_si_no_hay_hashes(cliente, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_keys_sha256", "")
    assert cliente.get("/api/v1/documentos").status_code != 401


def test_admite_varias_claves(cliente, monkeypatch):
    otra = hashlib.sha256(b"segunda-clave").hexdigest()
    monkeypatch.setattr(get_settings(), "api_keys_sha256", f"{HASH}, {otra}")
    assert cliente.get("/api/v1/documentos", headers={"X-API-Key": "segunda-clave"}).status_code != 401
