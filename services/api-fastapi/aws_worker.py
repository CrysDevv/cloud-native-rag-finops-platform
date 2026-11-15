import asyncio
import logging
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from aws_clients import AwsGateway
from config import Settings
from extractores import ErrorPermanente

logger = logging.getLogger("worker.aws")


class AwsWorkerGateway(AwsGateway):
    def __init__(self, settings: Settings, espera_sondeo: int = 20):
        super().__init__(settings)
        comunes = dict(
            region_name=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url or None,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        reintentos = {"max_attempts": 3, "mode": "standard"}
        self._sqs = boto3.client(
            "sqs", config=Config(connect_timeout=3, read_timeout=espera_sondeo + 15, retries=reintentos), **comunes)
        self._s3 = boto3.client(
            "s3", config=Config(connect_timeout=3, read_timeout=60, retries=reintentos), **comunes)

    async def descargar_s3(self, bucket: str, key: str, limite_bytes: int) -> bytes:
        def _obtener() -> bytes:
            try:
                resp = self._s3.get_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                codigo = exc.response.get("Error", {}).get("Code", "")
                if codigo in {"NoSuchKey", "NoSuchBucket", "404"}:
                    raise ErrorPermanente(f"El objeto s3://{bucket}/{key} no existe.") from exc
                raise
            if int(resp.get("ContentLength", 0)) > limite_bytes:
                raise ErrorPermanente("El objeto excede el tamaño máximo permitido para procesamiento.")
            datos = resp["Body"].read(limite_bytes + 1)
            if len(datos) > limite_bytes:
                raise ErrorPermanente("El objeto excede el tamaño máximo permitido para procesamiento.")
            return datos

        return await asyncio.to_thread(_obtener)

    async def recibir(self, nombre_cola: str, maximo: int, espera: int, visibilidad: int) -> list[dict[str, Any]]:
        def _recibir() -> list[dict[str, Any]]:
            resp = self._sqs.receive_message(
                QueueUrl=self._url_cola(nombre_cola),
                MaxNumberOfMessages=max(1, min(maximo, 10)),
                WaitTimeSeconds=max(0, min(espera, 20)),
                VisibilityTimeout=visibilidad,
                MessageAttributeNames=["All"],
                AttributeNames=["ApproximateReceiveCount"],
            )
            return resp.get("Messages", [])

        return await asyncio.to_thread(_recibir)

    async def eliminar(self, nombre_cola: str, receipt_handle: str) -> None:
        await asyncio.to_thread(
            self._sqs.delete_message, QueueUrl=self._url_cola(nombre_cola), ReceiptHandle=receipt_handle)

    async def cambiar_visibilidad(self, nombre_cola: str, receipt_handle: str, segundos: int) -> None:
        await asyncio.to_thread(
            self._sqs.change_message_visibility, QueueUrl=self._url_cola(nombre_cola),
            ReceiptHandle=receipt_handle, VisibilityTimeout=max(0, min(segundos, 43200)))
