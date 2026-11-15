import argparse
import asyncio
import json
import logging
import sys
import uuid

from sqlalchemy import text

from aws_clients import AwsGateway
from config import get_settings
from database import crear_engine, crear_sessionmaker
from middleware import configurar_logging

logger = logging.getLogger("reprocesar")

ESTADOS_VALIDOS = ("ERROR", "DLQ")

_SQL_CANDIDATOS = text("""
SELECT id, nombre_archivo, tipo_contenido, s3_bucket, s3_key, sha256
FROM documentos
WHERE estado = ANY(CAST(:estados AS text[]))
ORDER BY creado_en
LIMIT :limite
""")


async def ejecutar(estados: list[str], limite: int, simular: bool) -> int:
    settings = get_settings()
    configurar_logging(settings.log_level)
    engine = crear_engine(settings)
    sessionmaker = crear_sessionmaker(engine)
    gateway = AwsGateway(settings)
    cid = f"reproceso-{uuid.uuid4()}"
    publicados = fallidos = 0
    try:
        async with sessionmaker() as session:
            filas = (await session.execute(_SQL_CANDIDATOS, {"estados": estados, "limite": limite})
                     ).mappings().all()
        print(f"Documentos candidatos en estados {estados}: {len(filas)}")
        for f in filas:
            if simular:
                print(f"  [simulación] {f['id']}  {f['nombre_archivo']}")
                continue
            mensaje = json.dumps({
                "documento_id": str(f["id"]), "s3_bucket": f["s3_bucket"], "s3_key": f["s3_key"],
                "sha256": str(f["sha256"]).strip(), "nombre_archivo": f["nombre_archivo"],
                "tipo_contenido": f["tipo_contenido"], "correlation_id": cid,
            })
            try:
                message_id = await gateway.publicar_ingesta(mensaje, cid)
                publicados += 1
                print(f"  encolado {f['id']}  mensaje={message_id}")
            except Exception as exc:
                fallidos += 1
                print(f"  FALLO {f['id']}: {type(exc).__name__}", file=sys.stderr)
        if not simular:
            print(f"Resumen: encolados={publicados} fallidos={fallidos} correlation_id={cid}")
    finally:
        await engine.dispose()
    return 1 if fallidos else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Reencola documentos en estado ERROR o DLQ hacia SQS.")
    parser.add_argument("--estados", nargs="+", choices=ESTADOS_VALIDOS, default=list(ESTADOS_VALIDOS))
    parser.add_argument("--limite", type=int, default=100)
    parser.add_argument("--simular", action="store_true", help="Lista candidatos sin encolar.")
    args = parser.parse_args()
    sys.exit(asyncio.run(ejecutar(args.estados, max(1, min(args.limite, 1000)), args.simular)))


if __name__ == "__main__":
    main()
