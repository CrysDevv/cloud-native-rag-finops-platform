# Arquitectura

## Visión general

```mermaid
flowchart LR
    U[Cliente] -->|HTTP| API[API FastAPI]
    API -->|rate limit y caché| R[(Redis)]
    API -->|PutObject| S3[(S3 LocalStack)]
    API -->|SendMessage| Q[[SQS ingesta]]
    Q -->|ReceiveMessage| W[Worker de ingesta]
    Q -. maxReceiveCount .-> D[[SQS DLQ]]
    W -->|GetObject| S3
    W -->|fragmentos y vectores| PG[(PostgreSQL + pgvector)]
    API -->|búsqueda híbrida y auditoría| PG
    API -->|/metrics| P[Prometheus]
    W -->|:9100| P
    API -->|OTLP| O[OTel Collector]
```

## Flujo de ingesta

```mermaid
sequenceDiagram
    participant C as Cliente
    participant A as API
    participant S as S3
    participant Q as SQS
    participant W as Worker
    participant P as PostgreSQL
    C->>A: POST /api/v1/documentos
    A->>A: SHA-256 y verificación de duplicado
    A->>S: Subir archivo
    A->>P: INSERT documento PENDIENTE
    A->>Q: Mensaje con correlation_id
    A-->>C: 201 documento_id
    Q->>W: Mensaje
    W->>P: Reclamar documento (PROCESANDO)
    W->>S: Descargar y verificar SHA-256
    W->>W: Extraer, anonimizar PII, fragmentar, embeddings
    W->>P: Insertar fragmentos y marcar INDEXADO
    W->>Q: Eliminar mensaje
```

## Estados del documento

```mermaid
stateDiagram-v2
    [*] --> PENDIENTE
    PENDIENTE --> PROCESANDO: worker reclama
    PROCESANDO --> INDEXADO: éxito
    PROCESANDO --> ERROR: fallo permanente o transitorio
    ERROR --> PROCESANDO: reintento
    ERROR --> DLQ: intentos agotados
    DLQ --> PROCESANDO: reprocesar
    INDEXADO --> [*]
```

## Consulta RAG

1. La pregunta se anonimiza (RUT con módulo 11, correo, teléfono, tarjeta, cuenta).
2. Se consulta la caché exacta y luego la semántica (similitud coseno sobre el embedding).
3. Sin acierto, la búsqueda híbrida combina similitud `pgvector` (`1 - (embedding <=> vec)`) y `ts_rank_cd` en
   español mediante Reciprocal Rank Fusion ponderada, solo sobre documentos `INDEXADO`.
4. La síntesis extractiva elige la oración más afín de cada fragmento y devuelve citas con
   `documento_id`, `nombre_archivo`, `pagina`, `indice_fragmento`, `score` y `extracto`.
5. Se registra en `auditoria_consultas` y `metricas_finops` (costo estimado: 0 USD).

## Decisiones de diseño

- **Idempotencia**: el worker reclama documentos con `UPDATE ... RETURNING` condicionado al estado; un mensaje
  duplicado se omite y uno atascado en `PROCESANDO` se recupera tras el tiempo de visibilidad.
- **Reintentos**: los errores permanentes no se reintentan; los transitorios usan espera exponencial hasta la DLQ.
- **FinOps**: cada servicio registra consumo en `metricas_finops` con su límite de capa gratuita; el resumen
  compara el costo acumulado con `FINOPS_BUDGET_USD`.
- **Solo lectura**: la imagen no escribe fuera de `/tmp` (latido del worker incluido).

## Operación

| Situación | Acción |
|---|---|
| Documento en `ERROR` o `DLQ` | `python -m reprocesar --simular`, luego sin `--simular`. |
| Mensajes acumulados en la DLQ | Revisar `mensaje_error` del documento, corregir la causa y reprocesar. |
| API degradada (503 en `/health`) | Revisar `componentes` en la respuesta para ubicar el servicio caído. |
| Alertas | Reglas en `monitoring/prometheus/alert-rules.yml` (API caída, 5xx, latencia p95, worker, ingesta). |
