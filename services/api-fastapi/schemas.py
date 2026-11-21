from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

EstadoDocumento = Literal["PENDIENTE", "PROCESANDO", "INDEXADO", "ERROR", "DLQ"]


class DocumentoOut(BaseModel):
    id: UUID
    nombre_archivo: str
    titulo: str | None = None
    tipo_contenido: str | None = None
    s3_bucket: str
    s3_key: str
    sha256: str
    tamano_bytes: int | None = None
    num_paginas: int | None = None
    estado: EstadoDocumento
    intentos: int | None = None
    mensaje_error: str | None = None
    correlation_id: str | None = None
    pii_detectada: dict[str, Any] | None = None
    creado_en: datetime
    actualizado_en: datetime
    total_fragmentos: int | None = None


class DocumentoLista(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DocumentoOut]


class DocumentoCreado(BaseModel):
    documento_id: UUID
    estado: EstadoDocumento
    sha256: str
    s3_bucket: str
    s3_key: str
    sqs_message_id: str
    correlation_id: str


class ConsultaRequest(BaseModel):
    pregunta: str = Field(min_length=3, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class Cita(BaseModel):
    documento_id: UUID
    nombre_archivo: str
    creado_en: str | None = None
    pagina: int
    indice_fragmento: int
    score: float
    extracto: str


class ConsultaResponse(BaseModel):
    respuesta: str
    citas: list[Cita]
    fragmentos_recuperados: int
    cache_hit: bool
    pii_detectada: bool
    pii_resumen: dict[str, Any]
    pregunta_anonimizada: str
    latencia_ms: int
    correlation_id: str


class FinopsServicio(BaseModel):
    periodo: date
    servicio: str
    consumo_total: float
    costo_total_usd: float
    limite_capa_gratuita: float | None = None
    uso_capa_gratuita_pct: float | None = None
    alerta: bool = False


class FinopsResumen(BaseModel):
    generado_en: datetime
    dias: int
    presupuesto_usd: float
    costo_total_usd: float
    estado_presupuesto: Literal["DENTRO_DEL_PRESUPUESTO", "PRESUPUESTO_EXCEDIDO"]
    servicios: list[FinopsServicio]
    colas: dict[str, dict[str, Any]]
    sqs_max_receive_count: int
