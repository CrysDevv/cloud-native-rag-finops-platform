import asyncio
import json
import logging
import signal
import time
import uuid
from pathlib import Path
from typing import Any

from prometheus_client import start_http_server

import ingesta
from aws_worker import AwsWorkerGateway
from config import Settings
from database import crear_engine, crear_sessionmaker
from extractores import ErrorPermanente
from middleware import configurar_logging, correlation_id_ctx

logger = logging.getLogger("worker")


class WorkerSettings(Settings):
    worker_concurrency: int = 2
    worker_poll_wait_seconds: int = 20
    worker_visibility_timeout: int = 300
    worker_processing_timeout: int = 240
    worker_metrics_port: int = 9100
    worker_heartbeat_file: str = "/tmp/worker_heartbeat"
    worker_dlq_reconcile_seconds: int = 60


async def _dormir(parar: asyncio.Event, segundos: float) -> None:
    try:
        await asyncio.wait_for(parar.wait(), timeout=segundos)
    except asyncio.TimeoutError:
        pass


def _parsear(settings: Settings, mensaje: dict[str, Any]) -> tuple[str | None, str]:
    atributo = mensaje.get("MessageAttributes", {}).get(settings.correlation_id_header, {})
    cid = str(atributo.get("StringValue", "")).strip()
    try:
        cuerpo = json.loads(mensaje.get("Body", "{}"))
    except ValueError:
        cuerpo = {}
    if not isinstance(cuerpo, dict):
        cuerpo = {}
    cid = (cid or str(cuerpo.get("correlation_id", "")).strip() or str(uuid.uuid4()))[:128]
    try:
        documento_id = str(uuid.UUID(str(cuerpo.get("documento_id", ""))))
    except ValueError:
        documento_id = None
    return documento_id, cid


async def _latido(settings: WorkerSettings, parar: asyncio.Event) -> None:
    ruta = Path(settings.worker_heartbeat_file)
    while not parar.is_set():
        try:
            ruta.write_text(str(int(time.time())))
        except OSError:
            logger.warning("No fue posible escribir el archivo de latido")
        await _dormir(parar, 15)


async def _tratar_mensaje(settings: WorkerSettings, sessionmaker, gateway: AwsWorkerGateway,
                          mensaje: dict[str, Any]) -> None:
    cola = settings.sqs_queue_ingesta
    handle = mensaje["ReceiptHandle"]
    recuento = int(mensaje.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
    documento_id, cid = _parsear(settings, mensaje)
    token = correlation_id_ctx.set(cid)
    limite = max(30, min(settings.worker_processing_timeout, settings.worker_visibility_timeout - 30))
    try:
        if documento_id is None:
            logger.error("Mensaje de ingesta inválido; se descarta")
            ingesta.INGESTA_DOCS.labels("mensaje_invalido").inc()
            await gateway.eliminar(cola, handle)
            return
        try:
            resultado = await asyncio.wait_for(
                ingesta.procesar_documento(sessionmaker, gateway, settings, documento_id, cid,
                                           settings.worker_visibility_timeout),
                timeout=limite)
        except ErrorPermanente as exc:
            logger.error("Fallo permanente en la ingesta", extra={"extra_data": {
                "documento_id": documento_id, "error": str(exc)}})
            ingesta.INGESTA_DOCS.labels("error_permanente").inc()
            await ingesta.marcar_fallo(sessionmaker, documento_id, "ERROR", str(exc), cid)
            await gateway.eliminar(cola, handle)
            return
        except Exception as exc:
            ultimo = recuento >= settings.sqs_max_receive_count
            logger.exception("Fallo transitorio en la ingesta", extra={"extra_data": {
                "documento_id": documento_id, "intento": recuento, "ultimo_intento": ultimo}})
            ingesta.INGESTA_DOCS.labels("error_transitorio").inc()
            try:
                await ingesta.marcar_fallo(sessionmaker, documento_id, "DLQ" if ultimo else "ERROR",
                                           f"{type(exc).__name__}: {exc}", cid)
                espera = 5 if ultimo else min(30 * 2 ** (recuento - 1), 300)
                await gateway.cambiar_visibilidad(cola, handle, espera)
            except Exception:
                logger.exception("No fue posible registrar el fallo del documento")
            return

        if resultado == ingesta.RESULTADO_OCUPADO:
            logger.info("Documento en procesamiento por otro worker; se reintentará", extra={
                "extra_data": {"documento_id": documento_id}})
            return
        ingesta.INGESTA_DOCS.labels(resultado.lower()).inc()
        await gateway.eliminar(cola, handle)
    except Exception:
        logger.exception("Error inesperado al tratar el mensaje")
    finally:
        correlation_id_ctx.reset(token)


async def _bucle_ingesta(settings: WorkerSettings, sessionmaker, gateway: AwsWorkerGateway,
                         parar: asyncio.Event) -> None:
    espera_error = 1.0
    while not parar.is_set():
        try:
            mensajes = await gateway.recibir(settings.sqs_queue_ingesta, settings.worker_concurrency,
                                             settings.worker_poll_wait_seconds, settings.worker_visibility_timeout)
            espera_error = 1.0
        except Exception as exc:
            logger.warning("No fue posible consultar SQS: %s", type(exc).__name__)
            await _dormir(parar, espera_error)
            espera_error = min(espera_error * 2, 30.0)
            continue
        if mensajes:
            await asyncio.gather(*(_tratar_mensaje(settings, sessionmaker, gateway, m) for m in mensajes))


async def _bucle_dlq(settings: WorkerSettings, sessionmaker, gateway: AwsWorkerGateway,
                     parar: asyncio.Event) -> None:
    while not parar.is_set():
        await _dormir(parar, settings.worker_dlq_reconcile_seconds)
        if parar.is_set():
            break
        try:
            mensajes = await gateway.recibir(settings.sqs_queue_dlq, 10, 1, 30)
            for m in mensajes:
                documento_id, cid = _parsear(settings, m)
                if documento_id:
                    token = correlation_id_ctx.set(cid)
                    try:
                        await ingesta.conciliar_dlq(sessionmaker, documento_id)
                    finally:
                        correlation_id_ctx.reset(token)
        except Exception as exc:
            logger.warning("No fue posible conciliar la DLQ: %s", type(exc).__name__)


async def ejecutar() -> None:
    settings = WorkerSettings()
    configurar_logging(settings.log_level)
    engine = crear_engine(settings)
    sessionmaker = crear_sessionmaker(engine)
    gateway = AwsWorkerGateway(settings, settings.worker_poll_wait_seconds)
    parar = asyncio.Event()

    bucle = asyncio.get_running_loop()
    for senal in (signal.SIGINT, signal.SIGTERM):
        try:
            bucle.add_signal_handler(senal, parar.set)
        except NotImplementedError:
            signal.signal(senal, lambda *_: parar.set())

    start_http_server(settings.worker_metrics_port)
    logger.info("Worker de ingesta iniciado", extra={"extra_data": {
        "cola": settings.sqs_queue_ingesta, "dlq": settings.sqs_queue_dlq,
        "concurrencia": settings.worker_concurrency}})
    try:
        await asyncio.gather(
            _latido(settings, parar),
            _bucle_ingesta(settings, sessionmaker, gateway, parar),
            _bucle_dlq(settings, sessionmaker, gateway, parar),
        )
    finally:
        await engine.dispose()
        logger.info("Worker de ingesta detenido")


def main() -> None:
    asyncio.run(ejecutar())


if __name__ == "__main__":
    main()
