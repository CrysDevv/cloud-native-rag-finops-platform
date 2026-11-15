# cloud-native-rag-finops-platform

Plataforma RAG soberana con control de costos (FinOps), guardrail de privacidad para datos personales chilenos
(RUT, correo, teléfono, tarjetas y cuentas) y ejecución local sobre LocalStack. Sin dependencias de pago: los
embeddings y la síntesis de respuestas se calculan localmente.

## Componentes

| Componente | Descripción |
|---|---|
| `api` (FastAPI) | Carga de documentos, consulta RAG, resumen FinOps, `/health` y `/metrics`. |
| `worker` | Consume SQS, extrae texto (PDF, DOCX, HTML, TXT, MD), anonimiza, fragmenta e indexa en pgvector. |
| PostgreSQL 16 + pgvector | Documentos, fragmentos vectoriales (HNSW + `tsvector` en español), auditoría y métricas FinOps. |
| Redis 7 | Rate limiting por minuto y caché exacta/semántica de consultas. |
| LocalStack | S3 (documentos), SQS (cola de ingesta + DLQ) y Secrets Manager. |
| Prometheus + OTel Collector | Métricas de API y worker, reglas de alerta y recepción de trazas OTLP. |

## Requisitos

- Docker y Docker Compose >= 2.24 (el servicio `worker` usa la etiqueta `!reset`).
- Python 3.12 solo si se ejecutan las pruebas fuera de Docker.
- Git Bash en Windows para los scripts `.sh`.

## Arranque rápido

```bash
cp .env.example .env
make levantar
make humo
```

`make humo` ejecuta `scripts/smoke_test.sh`: verifica salud, sube un documento, espera su indexación, consulta el
RAG, comprueba que no se expone PII y valida la caché y el resumen FinOps. La API escucha en el puerto 8000
(`API_URL` permite cambiar el destino).

## Endpoints

| Método | Ruta | Función |
|---|---|---|
| GET | `/health` | Estado de PostgreSQL, Redis y LocalStack (200 o 503). |
| GET | `/metrics` | Métricas Prometheus. |
| POST | `/api/v1/documentos` | Carga multipart; deduplica por SHA-256, guarda en S3 y encola en SQS. |
| GET | `/api/v1/documentos` | Lista con filtro `estado`, `limit` y `offset`. |
| GET | `/api/v1/documentos/{id}` | Estado de ingesta de un documento. |
| POST | `/api/v1/rag/consultar` | Consulta con citas verificables; anonimiza la pregunta. |
| GET | `/api/v1/finops/resumen` | Consumo por servicio, presupuesto y estado de colas SQS/DLQ. |

Todos los errores usan RFC 7807 (`application/problem+json`) e incluyen `correlation_id`. El encabezado
`X-Correlation-ID` se genera si falta, se devuelve en la respuesta, aparece en los logs JSON y viaja como
atributo del mensaje SQS.

## Ciclo de vida de un documento

`PENDIENTE` -> `PROCESANDO` -> `INDEXADO`. Un fallo permanente (archivo corrupto, cifrado, sin texto, hash
distinto) deja el documento en `ERROR`. Un fallo transitorio se reintenta con espera creciente; al alcanzar
`SQS_MAX_RECEIVE_COUNT` el mensaje pasa a la DLQ y el documento se concilia como `DLQ`.

Para reencolar documentos en `ERROR` o `DLQ`:

```bash
docker compose -f docker-compose.yml -f docker-compose.worker.yml exec worker python -m reprocesar --simular
docker compose -f docker-compose.yml -f docker-compose.worker.yml exec worker python -m reprocesar --estados DLQ
```

## Pruebas

```bash
pip install -r services/api-fastapi/requirements-dev.txt
make pruebas
```

El pipeline `.github/workflows/devsecops-pipeline.yml` ejecuta pruebas, Bandit, Gitleaks, hadolint, Trivy y la
validación de Terraform y Compose.

## Seguridad

- Contenedor non-root (UID 10001), sistema de archivos de solo lectura, `cap_drop: [ALL]` y temporales en `/tmp`.
- Los textos se anonimizan antes de fragmentarse, indexarse y auditarse; el RUT se valida con módulo 11.
- Rate limiting por IP y minuto con Redis (`REDIS_QUOTA_REQUESTS_PER_MINUTE`).

## Limitaciones conocidas

- La API no incluye autenticación ni autorización; debe publicarse detrás de un API gateway o proxy con control de acceso.
- Los embeddings deterministas capturan coincidencias léxicas y de n-gramas, no semántica profunda. Para mayor
  calidad se debe incorporar un proveedor de embeddings real y actualizar `EMBEDDING_PROVIDER` y `EMBEDDING_DIMENSIONS`.
- La síntesis `local-extractive` selecciona oraciones de los fragmentos; no redacta ni razona.
- No hay OCR: los PDF escaneados sin capa de texto terminan en `ERROR`.
- La caché semántica usa un recorrido acotado de las últimas entradas (no un índice vectorial).

## Versiones

`v0.1.0` infraestructura base, `v0.2.0` backend y RAG, `v0.2.1` worker de ingesta, `v0.3.0` imagen Docker,
pipeline y monitoreo, `v0.4.0` pruebas de contrato, operación y documentación.
