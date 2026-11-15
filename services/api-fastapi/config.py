import os
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sistema de archivos de solo lectura: todo archivo temporal vive en /tmp
os.environ.setdefault("TMPDIR", "/tmp")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/rag"
    redis_url: str = "redis://redis:6379/0"

    aws_region: str = "us-east-2"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    aws_endpoint_url: str = "http://localstack:4566"
    s3_bucket_documentos: str = "rag-documentos-local"
    sqs_queue_ingesta: str = "rag-ingesta-queue"
    sqs_queue_dlq: str = "rag-ingesta-dlq"
    sqs_max_receive_count: int = 3

    embedding_provider: str = "local-deterministic"
    embedding_dimensions: int = 384
    llm_provider: str = "local-extractive"
    rag_top_k: int = 5
    chunk_size: int = 800
    chunk_overlap: int = 120
    pii_anonymize_enabled: bool = True

    finops_budget_usd: float = 0.0
    redis_quota_requests_per_minute: int = 60

    otel_exporter_otlp_endpoint: str = "http://otel-collector:4317"
    otel_service_name: str = "api-fastapi"
    correlation_id_header: str = "X-Correlation-ID"

    max_upload_mb: int = 25
    api_keys_sha256: str = ""
    cache_ttl_segundos: int = 3600
    semantic_cache_umbral: float = 0.97
    semantic_cache_max_entradas: int = 200
    rag_candidatos: int = 40
    rrf_k: int = 60
    peso_vectorial: float = 0.6
    peso_lexico: float = 0.4

    @field_validator("database_url")
    @classmethod
    def _normalizar_dsn(cls, valor: str) -> str:
        for prefijo in ("postgresql://", "postgres://"):
            if valor.startswith(prefijo):
                return "postgresql+asyncpg://" + valor[len(prefijo):]
        return valor


@lru_cache
def get_settings() -> Settings:
    return Settings()
