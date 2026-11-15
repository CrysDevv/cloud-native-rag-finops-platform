import hashlib
import json
import logging
import mimetypes
import os
import re
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import rag_engine
from aws_clients import AwsGateway
from config import Settings, get_settings
from middleware import ProblemaHTTP
from schemas import (ConsultaRequest, ConsultaResponse, DocumentoCreado, DocumentoLista, DocumentoOut,
                     EstadoDocumento, FinopsResumen, FinopsServicio)

logger = logging.getLogger("api.routers")

router_documentos = APIRouter(prefix="/api/v1/documentos", tags=["documentos"])
router_rag = APIRouter(prefix="/api/v1/rag", tags=["rag"])
router_finops = APIRouter(prefix="/api/v1/finops", tags=["finops"])

EXTENSIONES_PERMITIDAS = {".pdf", ".txt", ".md", ".docx", ".html"}
LIMITE_S3_PUT_MES = 2000.0
LIMITE_SQS_REQ_MES = 1_000_000.0

COLUMNAS_DOC = """d.id, d.nombre_archivo, d.titulo, d.tipo_contenido, d.s3_bucket, d.s3_key, d.sha256,
    d.tamano_bytes, d.num_paginas, d.estado, d.intentos, d.mensaje_error, d.correlation_id, d.pii_detectada,
    d.creado_en, d.actualizado_en,
    (SELECT count(*) FROM fragmentos_vectoriales f WHERE f.documento_id = d.id) AS total_fragmentos"""


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_redis(request: Request):
    return request.app.state.redis


def get_gateway(request: Request) -> AwsGateway:
    return request.app.state.gateway


def _cid(request: Request) -> str:
    return request.state.correlation_id


@router_documentos.post("", response_model=DocumentoCreado, status_code=201)
async def subir_documento(
    request: Request,
    archivo: UploadFile = File(...),
    titulo: str | None = Form(default=None, max_length=300),
    session: AsyncSession = Depends(get_session),
    gateway: AwsGateway = Depends(get_gateway),
    settings: Settings = Depends(get_settings),
):
    cid = _cid(request)
    nombre = os.path.basename(archivo.filename or "").strip()
    if not nombre:
        raise ProblemaHTTP(400, "Archivo inválido", "El archivo debe incluir un nombre.")
    extension = os.path.splitext(nombre)[1].lower()
    if extension not in EXTENSIONES_PERMITIDAS:
        raise ProblemaHTTP(415, "Tipo de archivo no soportado",
                           f"Extensiones permitidas: {', '.join(sorted(EXTENSIONES_PERMITIDAS))}.")

    hasher = hashlib.sha256()
    buffer = bytearray()
    limite = settings.max_upload_mb * 1024 * 1024
    while True:
        trozo = await archivo.read(1024 * 1024)
        if not trozo:
            break
        if len(buffer) + len(trozo) > limite:
            raise ProblemaHTTP(413, "Archivo demasiado grande", f"El tamaño máximo es {settings.max_upload_mb} MB.")
        hasher.update(trozo)
        buffer.extend(trozo)
    if not buffer:
        raise ProblemaHTTP(400, "Archivo vacío", "El archivo no contiene datos.")
    sha256 = hasher.hexdigest()

    existente = (await session.execute(
        text("SELECT id, estado FROM documentos WHERE sha256 = :h"), {"h": sha256})).mappings().first()
    if existente:
        raise ProblemaHTTP(409, "Documento duplicado", "Ya existe un documento con el mismo SHA-256.",
                           {"documento_id": str(existente["id"]), "estado": existente["estado"]})

    tipo = archivo.content_type or mimetypes.guess_type(nombre)[0] or "application/octet-stream"
    seguro = re.sub(r"[^A-Za-z0-9._-]+", "_", nombre)[:150]
    key = f"documentos/{sha256[:2]}/{sha256}/{seguro}"
    documento_id = uuid.uuid4()
    titulo_final = (titulo or os.path.splitext(nombre)[0])[:300]

    try:
        await gateway.subir_s3(key, bytes(buffer), tipo, {"sha256": sha256, "correlation-id": cid})
    except Exception as exc:
        logger.exception("Fallo al subir a S3")
        raise ProblemaHTTP(502, "Error de almacenamiento", "No fue posible almacenar el archivo en S3.") from exc

    try:
        await session.execute(text("""
            INSERT INTO documentos (id, nombre_archivo, titulo, tipo_contenido, s3_bucket, s3_key, sha256,
                tamano_bytes, num_paginas, estado, intentos, mensaje_error, correlation_id, pii_detectada,
                creado_en, actualizado_en)
            VALUES (CAST(:id AS uuid), :nombre, :titulo, :tipo, :bucket, :key, :sha, :tam, NULL, 'PENDIENTE', 0,
                NULL, :cid, CAST('{}' AS jsonb), now(), now())
        """), {"id": str(documento_id), "nombre": nombre, "titulo": titulo_final, "tipo": tipo,
               "bucket": settings.s3_bucket_documentos, "key": key, "sha": sha256, "tam": len(buffer), "cid": cid})
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemaHTTP(409, "Documento duplicado", "El documento ya fue registrado previamente.") from exc

    mensaje = json.dumps({
        "documento_id": str(documento_id), "s3_bucket": settings.s3_bucket_documentos, "s3_key": key,
        "sha256": sha256, "nombre_archivo": nombre, "tipo_contenido": tipo, "correlation_id": cid,
    })
    try:
        message_id = await gateway.publicar_ingesta(mensaje, cid)
    except Exception as exc:
        logger.exception("Fallo al publicar en SQS")
        await session.execute(text("""
            UPDATE documentos SET estado = 'ERROR', mensaje_error = :m, actualizado_en = now()
            WHERE id = CAST(:id AS uuid)"""), {"m": "No se pudo encolar la ingesta en SQS", "id": str(documento_id)})
        await session.commit()
        raise ProblemaHTTP(502, "Error de mensajería", "El documento se almacenó pero no pudo encolarse.",
                           {"documento_id": str(documento_id)}) from exc

    await rag_engine.registrar_metrica(session, "s3", "put_requests", 1, "requests", cid,
                                       limite=LIMITE_S3_PUT_MES, metadatos={"bytes": len(buffer)})
    await rag_engine.registrar_metrica(session, "s3", "bytes_almacenados", len(buffer), "bytes", cid)
    await rag_engine.registrar_metrica(session, "sqs", "mensajes_publicados", 1, "requests", cid,
                                       limite=LIMITE_SQS_REQ_MES, metadatos={"cola": settings.sqs_queue_ingesta})
    await rag_engine.registrar_metrica(session, "postgres", "documentos_registrados", 1, "filas", cid)
    await session.commit()

    return DocumentoCreado(documento_id=documento_id, estado="PENDIENTE", sha256=sha256,
                           s3_bucket=settings.s3_bucket_documentos, s3_key=key,
                           sqs_message_id=message_id, correlation_id=cid)


@router_documentos.get("", response_model=DocumentoLista)
async def listar_documentos(
    estado: EstadoDocumento | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    total = (await session.execute(
        text("SELECT count(*) FROM documentos d WHERE (CAST(:e AS text) IS NULL OR d.estado = CAST(:e AS text))"),
        {"e": estado})).scalar_one()
    filas = (await session.execute(text(f"""
        SELECT {COLUMNAS_DOC} FROM documentos d
        WHERE (CAST(:e AS text) IS NULL OR d.estado = CAST(:e AS text))
        ORDER BY d.creado_en DESC LIMIT :limit OFFSET :offset
    """), {"e": estado, "limit": limit, "offset": offset})).mappings().all()
    return DocumentoLista(total=total, limit=limit, offset=offset,
                          items=[DocumentoOut.model_validate(dict(f)) for f in filas])


@router_documentos.get("/{documento_id}", response_model=DocumentoOut)
async def detalle_documento(documento_id: UUID, session: AsyncSession = Depends(get_session)):
    fila = (await session.execute(
        text(f"SELECT {COLUMNAS_DOC} FROM documentos d WHERE d.id = CAST(:id AS uuid)"),
        {"id": str(documento_id)})).mappings().first()
    if not fila:
        raise ProblemaHTTP(404, "Documento no encontrado", f"No existe el documento {documento_id}.")
    return DocumentoOut.model_validate(dict(fila))


@router_rag.post("/consultar", response_model=ConsultaResponse)
async def consultar_rag(
    cuerpo: ConsultaRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis=Depends(get_redis),
    settings: Settings = Depends(get_settings),
):
    resultado = await rag_engine.consultar(session, redis, settings, cuerpo.pregunta, cuerpo.top_k, _cid(request))
    return ConsultaResponse.model_validate(resultado)


@router_finops.get("/resumen", response_model=FinopsResumen)
async def resumen_finops(
    dias: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    gateway: AwsGateway = Depends(get_gateway),
    settings: Settings = Depends(get_settings),
):
    filas = (await session.execute(text("""
        SELECT periodo, servicio, consumo_total, costo_total_usd, limite_capa_gratuita
        FROM vw_finops_resumen_diario
        WHERE periodo >= CURRENT_DATE - CAST(:dias AS integer)
        ORDER BY periodo DESC, servicio
    """), {"dias": dias})).mappings().all()

    servicios: list[FinopsServicio] = []
    costo_total = 0.0
    for f in filas:
        consumo = float(f["consumo_total"] or 0)
        costo = float(f["costo_total_usd"] or 0)
        limite = float(f["limite_capa_gratuita"]) if f["limite_capa_gratuita"] is not None else None
        pct = round(consumo / limite * 100, 2) if limite else None
        costo_total += costo
        servicios.append(FinopsServicio(periodo=f["periodo"], servicio=f["servicio"], consumo_total=consumo,
                                        costo_total_usd=costo, limite_capa_gratuita=limite,
                                        uso_capa_gratuita_pct=pct, alerta=bool(pct is not None and pct >= 80)))

    ingesta = await gateway.estado_cola(settings.sqs_queue_ingesta)
    dlq = await gateway.estado_cola(settings.sqs_queue_dlq)
    estado = "DENTRO_DEL_PRESUPUESTO" if costo_total <= settings.finops_budget_usd else "PRESUPUESTO_EXCEDIDO"
    return FinopsResumen(generado_en=datetime.now(timezone.utc), dias=dias,
                         presupuesto_usd=settings.finops_budget_usd, costo_total_usd=round(costo_total, 6),
                         estado_presupuesto=estado, servicios=servicios,
                         colas={"ingesta": ingesta, "dlq": dlq},
                         sqs_max_receive_count=settings.sqs_max_receive_count)
