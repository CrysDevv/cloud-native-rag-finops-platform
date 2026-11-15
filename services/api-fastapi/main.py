import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from aws_clients import AwsGateway
from config import get_settings
from database import crear_engine, crear_redis, crear_sessionmaker
from middleware import configurar_logging, registrar_middlewares
from routers import router_documentos, router_finops, router_rag
from seguridad import exigir_api_key

settings = get_settings()
configurar_logging(settings.log_level)
logger = logging.getLogger("api")


def _configurar_otel(app: FastAPI) -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        proveedor = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name,
                                                             "deployment.environment": settings.app_env}))
        proveedor.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)))
        trace.set_tracer_provider(proveedor)
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    except Exception:
        logger.warning("OpenTelemetry no pudo inicializarse; el servicio continúa sin trazas")


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = crear_engine(settings)
    app.state.engine = engine
    app.state.sessionmaker = crear_sessionmaker(engine)
    app.state.redis = crear_redis(settings)
    app.state.gateway = AwsGateway(settings)
    logger.info("Servicio iniciado", extra={"extra_data": {"entorno": settings.app_env}})
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await engine.dispose()
        logger.info("Servicio detenido")


app = FastAPI(title="cloud-native-rag-finops-platform", version="0.2.0", lifespan=lifespan)
registrar_middlewares(app)
_configurar_otel(app)
app.include_router(router_documentos, dependencies=[Depends(exigir_api_key)])
app.include_router(router_rag, dependencies=[Depends(exigir_api_key)])
app.include_router(router_finops, dependencies=[Depends(exigir_api_key)])


async def _chequear(coro) -> str:
    try:
        await asyncio.wait_for(coro, timeout=3)
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


@app.get("/health", tags=["operacion"])
async def health():
    async def pg():
        async with app.state.sessionmaker() as s:
            await s.execute(text("SELECT 1"))

    postgres, redis, localstack = await asyncio.gather(
        _chequear(pg()), _chequear(app.state.redis.ping()), _chequear(app.state.gateway.verificar_s3()))
    componentes = {"postgresql": postgres, "redis": redis, "localstack": localstack}
    sano = all(v == "ok" for v in componentes.values())
    return JSONResponse({"status": "ok" if sano else "degradado", "componentes": componentes},
                        status_code=200 if sano else 503)


@app.get("/metrics", tags=["operacion"], include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
