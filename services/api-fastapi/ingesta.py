import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections import Counter as Conteo
from typing import Any

from prometheus_client import Counter, Histogram
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aws_worker import AwsWorkerGateway
from chunker import dividir_texto
from config import Settings
from extractores import ErrorExtraccion, ErrorPermanente, Extraccion, extraer_paginas
from pii_guardrail import anonimizar
from rag_engine import generar_embedding, literal_vector, registrar_metrica

logger = logging.getLogger("worker.ingesta")

INGESTA_DOCS = Counter("ingesta_documentos_total", "Documentos procesados por el worker", ["resultado"])
INGESTA_FRAGMENTOS = Counter("ingesta_fragmentos_total", "Fragmentos vectoriales insertados")
INGESTA_DURACION = Histogram("ingesta_duracion_seconds", "Duración de la ingesta por documento")

RESULTADO_INDEXADO = "INDEXADO"
RESULTADO_OMITIDO = "OMITIDO"
RESULTADO_HUERFANO = "HUERFANO"
RESULTADO_OCUPADO = "OCUPADO"

LIMITE_S3_GET_MES = 20000.0
LOTE_INSERCION = 200
_RE_TOKEN_PII = re.compile(r"\[(?:RUT|EMAIL|TELEFONO|TARJETA|CUENTA)_ANONIMIZAD[OA]\]")


class ErrorIntegridad(ErrorPermanente):
    pass


_SQL_RECLAMAR = text("""
UPDATE documentos
SET estado = 'PROCESANDO', intentos = COALESCE(intentos, 0) + 1, mensaje_error = NULL, actualizado_en = now()
WHERE id = CAST(:id AS uuid)
  AND (estado IN ('PENDIENTE', 'ERROR', 'DLQ')
       OR (estado = 'PROCESANDO'
           AND actualizado_en < now() - make_interval(secs => CAST(:stale AS double precision))))
RETURNING id, nombre_archivo, tipo_contenido, s3_bucket, s3_key, sha256, intentos
""")

_SQL_ESTADO = text("SELECT estado FROM documentos WHERE id = CAST(:id AS uuid)")

_SQL_BORRAR_FRAGMENTOS = text("DELETE FROM fragmentos_vectoriales WHERE documento_id = CAST(:id AS uuid)")

_SQL_INSERTAR_FRAGMENTO = text("""
INSERT INTO fragmentos_vectoriales (id, documento_id, pagina, indice_fragmento, contenido_anonimizado,
                                    embedding, num_tokens, pii_reemplazos, metadatos, creado_en)
VALUES (CAST(:id AS uuid), CAST(:doc AS uuid), :pagina, :indice, :contenido,
        CAST(CAST(:emb AS text) AS vector), :tokens, :pii, CAST(:meta AS jsonb), now())
""")

_SQL_INDEXAR = text("""
UPDATE documentos
SET estado = 'INDEXADO', num_paginas = :paginas, pii_detectada = CAST(:pii AS jsonb),
    mensaje_error = NULL, actualizado_en = now()
WHERE id = CAST(:id AS uuid) AND estado = 'PROCESANDO'
""")

_SQL_FALLO = text("""
UPDATE documentos SET estado = :estado, mensaje_error = :mensaje, actualizado_en = now()
WHERE id = CAST(:id AS uuid) AND estado <> 'INDEXADO'
""")

_SQL_CONCILIAR_DLQ = text("""
UPDATE documentos
SET estado = 'DLQ', mensaje_error = COALESCE(mensaje_error, 'Mensaje movido a la DLQ de SQS'),
    actualizado_en = now()
WHERE id = CAST(:id AS uuid) AND estado IN ('PENDIENTE', 'ERROR')
""")


def preparar_fragmentos(extraccion: Extraccion, settings: Settings) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fragmentos: list[dict[str, Any]] = []
    por_tipo: Conteo = Conteo()
    total_pii = 0
    indice = 0
    for numero, contenido in extraccion.paginas:
        anon = anonimizar(contenido, settings.pii_anonymize_enabled)
        total_pii += anon.total
        por_tipo.update(anon.por_tipo)
        for trozo in dividir_texto(anon.texto, settings.chunk_size, settings.chunk_overlap):
            vector = generar_embedding(trozo, settings.embedding_dimensions)
            fragmentos.append({
                "id": str(uuid.uuid4()),
                "pagina": numero,
                "indice": indice,
                "contenido": trozo,
                "emb": literal_vector(vector),
                "tokens": len(trozo.split()),
                "pii": len(_RE_TOKEN_PII.findall(trozo)),
                "meta": json.dumps({"extractor": extraccion.extractor, "caracteres": len(trozo)}),
            })
            indice += 1
    if not fragmentos:
        raise ErrorExtraccion("No se generaron fragmentos a partir del documento.")
    resumen = {"detectada": total_pii > 0, "total": total_pii, "por_tipo": dict(por_tipo)}
    return fragmentos, resumen


async def procesar_documento(sessionmaker: async_sessionmaker, gateway: AwsWorkerGateway, settings: Settings,
                             documento_id: str, correlation_id: str, stale_segundos: float) -> str:
    inicio = time.perf_counter()
    async with sessionmaker() as session:
        fila = (await session.execute(_SQL_RECLAMAR, {"id": documento_id, "stale": float(stale_segundos)})
                ).mappings().first()
        doc = dict(fila) if fila else None
        await session.commit()
        if doc is None:
            estado = (await session.execute(_SQL_ESTADO, {"id": documento_id})).scalar_one_or_none()
            await session.rollback()
            if estado is None:
                return RESULTADO_HUERFANO
            return RESULTADO_OCUPADO if estado == "PROCESANDO" else RESULTADO_OMITIDO

    limite_bytes = settings.max_upload_mb * 1024 * 1024
    datos = await gateway.descargar_s3(doc["s3_bucket"], doc["s3_key"], limite_bytes)
    sha_calculado = await asyncio.to_thread(lambda: hashlib.sha256(datos).hexdigest())
    if sha_calculado != str(doc["sha256"]).strip():
        raise ErrorIntegridad("El SHA-256 del objeto en S3 no coincide con el registrado en la base de datos.")

    extraccion = await asyncio.to_thread(extraer_paginas, datos, doc["nombre_archivo"])
    fragmentos, resumen_pii = await asyncio.to_thread(preparar_fragmentos, extraccion, settings)

    async with sessionmaker() as session:
        await session.execute(_SQL_BORRAR_FRAGMENTOS, {"id": documento_id})
        for i in range(0, len(fragmentos), LOTE_INSERCION):
            lote = [{**f, "doc": documento_id} for f in fragmentos[i:i + LOTE_INSERCION]]
            await session.execute(_SQL_INSERTAR_FRAGMENTO, lote)
        resultado = await session.execute(_SQL_INDEXAR, {
            "paginas": extraccion.num_paginas, "pii": json.dumps(resumen_pii), "id": documento_id})
        if resultado.rowcount == 0:
            raise RuntimeError("El estado del documento cambió durante el procesamiento.")
        duracion_ms = int((time.perf_counter() - inicio) * 1000)
        await registrar_metrica(session, "worker", "documentos_procesados", 1, "documentos", correlation_id,
                                metadatos={"fragmentos": len(fragmentos), "duracion_ms": duracion_ms})
        await registrar_metrica(session, "embeddings", "embeddings_generados", len(fragmentos), "vectores",
                                correlation_id, metadatos={"proveedor": settings.embedding_provider})
        await registrar_metrica(session, "s3", "get_requests", 1, "requests", correlation_id,
                                limite=LIMITE_S3_GET_MES, metadatos={"bytes": len(datos)})
        await registrar_metrica(session, "postgres", "fragmentos_insertados", len(fragmentos), "filas",
                                correlation_id)
        await session.commit()

    INGESTA_DOCS.labels("indexado").inc()
    INGESTA_FRAGMENTOS.inc(len(fragmentos))
    INGESTA_DURACION.observe(time.perf_counter() - inicio)
    logger.info("documento_indexado", extra={"extra_data": {
        "documento_id": documento_id, "fragmentos": len(fragmentos), "paginas": extraccion.num_paginas,
        "pii_total": resumen_pii["total"], "duracion_ms": duracion_ms, "intentos": doc["intentos"]}})
    return RESULTADO_INDEXADO


async def marcar_fallo(sessionmaker: async_sessionmaker, documento_id: str, estado: str, mensaje: str,
                       correlation_id: str) -> None:
    async with sessionmaker() as session:
        await session.execute(_SQL_FALLO, {"estado": estado, "mensaje": mensaje[:500], "id": documento_id})
        await registrar_metrica(session, "worker", "documentos_fallidos", 1, "documentos", correlation_id,
                                metadatos={"estado": estado})
        await session.commit()


async def conciliar_dlq(sessionmaker: async_sessionmaker, documento_id: str) -> None:
    async with sessionmaker() as session:
        await session.execute(_SQL_CONCILIAR_DLQ, {"id": documento_id})
        await session.commit()
