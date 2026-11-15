-- ============================================================================
-- cloud-native-rag-finops-platform — Esquema inicial PostgreSQL 16 + pgvector
-- Dimensión de embeddings: 384 (embeddings locales deterministas)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Función genérica de actualización de marca de tiempo
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_actualizar_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.actualizado_en = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- documentos: registro de cada archivo cargado a S3
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documentos (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre_archivo    TEXT        NOT NULL,
    titulo            TEXT,
    tipo_contenido    TEXT        NOT NULL DEFAULT 'application/pdf',
    s3_bucket         TEXT        NOT NULL,
    s3_key            TEXT        NOT NULL,
    sha256            CHAR(64)    NOT NULL,
    tamano_bytes      BIGINT      NOT NULL CHECK (tamano_bytes >= 0),
    num_paginas       INTEGER     CHECK (num_paginas IS NULL OR num_paginas > 0),
    estado            TEXT        NOT NULL DEFAULT 'PENDIENTE'
                      CHECK (estado IN ('PENDIENTE', 'PROCESANDO', 'INDEXADO', 'ERROR', 'DLQ')),
    intentos          INTEGER     NOT NULL DEFAULT 0 CHECK (intentos >= 0),
    mensaje_error     TEXT,
    correlation_id    TEXT        NOT NULL,
    pii_detectada     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    creado_en         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_documentos_s3 UNIQUE (s3_bucket, s3_key),
    CONSTRAINT uq_documentos_sha256 UNIQUE (sha256)
);

CREATE INDEX IF NOT EXISTS idx_documentos_estado ON documentos (estado);
CREATE INDEX IF NOT EXISTS idx_documentos_correlation ON documentos (correlation_id);
CREATE INDEX IF NOT EXISTS idx_documentos_creado_en ON documentos (creado_en DESC);

CREATE TRIGGER trg_documentos_actualizado
    BEFORE UPDATE ON documentos
    FOR EACH ROW EXECUTE FUNCTION fn_actualizar_timestamp();

-- ---------------------------------------------------------------------------
-- fragmentos_vectoriales: chunks anonimizados + embedding + texto completo
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fragmentos_vectoriales (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    documento_id          UUID        NOT NULL REFERENCES documentos (id) ON DELETE CASCADE,
    pagina                INTEGER     NOT NULL CHECK (pagina > 0),
    indice_fragmento      INTEGER     NOT NULL CHECK (indice_fragmento >= 0),
    contenido_anonimizado TEXT        NOT NULL,
    embedding             vector(384) NOT NULL,
    tsv                   tsvector GENERATED ALWAYS AS (
                              to_tsvector('spanish', coalesce(contenido_anonimizado, ''))
                          ) STORED,
    num_tokens            INTEGER     NOT NULL DEFAULT 0 CHECK (num_tokens >= 0),
    pii_reemplazos        INTEGER     NOT NULL DEFAULT 0 CHECK (pii_reemplazos >= 0),
    metadatos             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    creado_en             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fragmento_posicion UNIQUE (documento_id, pagina, indice_fragmento)
);

-- Búsqueda semántica aproximada (HNSW, distancia coseno)
CREATE INDEX IF NOT EXISTS idx_fragmentos_embedding_hnsw
    ON fragmentos_vectoriales
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Búsqueda de texto completo (parte léxica de la búsqueda híbrida)
CREATE INDEX IF NOT EXISTS idx_fragmentos_tsv_gin
    ON fragmentos_vectoriales USING gin (tsv);

-- Similitud trigram para tolerancia a errores tipográficos
CREATE INDEX IF NOT EXISTS idx_fragmentos_contenido_trgm
    ON fragmentos_vectoriales USING gin (contenido_anonimizado gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_fragmentos_documento
    ON fragmentos_vectoriales (documento_id, pagina);

-- ---------------------------------------------------------------------------
-- auditoria_consultas: trazabilidad de cada consulta RAG (sin PII)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auditoria_consultas (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    correlation_id           TEXT        NOT NULL,
    pregunta_anonimizada     TEXT        NOT NULL,
    respuesta                TEXT,
    citas                    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    fragmentos_recuperados   INTEGER     NOT NULL DEFAULT 0 CHECK (fragmentos_recuperados >= 0),
    cache_hit                BOOLEAN     NOT NULL DEFAULT FALSE,
    pii_detectada            BOOLEAN     NOT NULL DEFAULT FALSE,
    latencia_ms              INTEGER     NOT NULL DEFAULT 0 CHECK (latencia_ms >= 0),
    proveedor_embedding      TEXT        NOT NULL DEFAULT 'local-deterministic',
    proveedor_llm            TEXT        NOT NULL DEFAULT 'local-extractive',
    tokens_entrada           INTEGER     NOT NULL DEFAULT 0 CHECK (tokens_entrada >= 0),
    tokens_salida            INTEGER     NOT NULL DEFAULT 0 CHECK (tokens_salida >= 0),
    costo_estimado_usd       NUMERIC(12, 6) NOT NULL DEFAULT 0 CHECK (costo_estimado_usd >= 0),
    estado_http              INTEGER     NOT NULL DEFAULT 200,
    creado_en                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auditoria_correlation ON auditoria_consultas (correlation_id);
CREATE INDEX IF NOT EXISTS idx_auditoria_creado_en ON auditoria_consultas (creado_en DESC);
CREATE INDEX IF NOT EXISTS idx_auditoria_cache_hit ON auditoria_consultas (cache_hit);

-- ---------------------------------------------------------------------------
-- metricas_finops: consumo y costo estimado por servicio
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metricas_finops (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    servicio            TEXT        NOT NULL
                        CHECK (servicio IN ('s3', 'sqs', 'secretsmanager', 'postgres', 'redis', 'embeddings', 'llm', 'api', 'worker')),
    metrica             TEXT        NOT NULL,
    valor               NUMERIC(18, 4) NOT NULL DEFAULT 0,
    unidad              TEXT        NOT NULL DEFAULT 'unidades',
    costo_estimado_usd  NUMERIC(12, 6) NOT NULL DEFAULT 0 CHECK (costo_estimado_usd >= 0),
    limite_capa_gratuita NUMERIC(18, 4),
    correlation_id      TEXT,
    periodo             DATE        NOT NULL DEFAULT CURRENT_DATE,
    metadatos           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_finops_servicio_periodo ON metricas_finops (servicio, periodo DESC);
CREATE INDEX IF NOT EXISTS idx_finops_creado_en ON metricas_finops (creado_en DESC);

-- ---------------------------------------------------------------------------
-- Vista de resumen FinOps diario
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_finops_resumen_diario AS
SELECT
    periodo,
    servicio,
    SUM(valor)               AS consumo_total,
    SUM(costo_estimado_usd)  AS costo_total_usd,
    MAX(limite_capa_gratuita) AS limite_capa_gratuita
FROM metricas_finops
GROUP BY periodo, servicio;
