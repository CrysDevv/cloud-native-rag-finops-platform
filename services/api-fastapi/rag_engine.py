import hashlib
import json
import logging
import math
import re
import time
import unicodedata
import uuid
from typing import Any

from prometheus_client import Counter, Histogram
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from pii_guardrail import anonimizar

logger = logging.getLogger("api.rag")

RAG_CONSULTAS = Counter("rag_consultas_total", "Consultas RAG", ["cache_hit"])
RAG_LATENCIA = Histogram("rag_consulta_duracion_seconds", "Latencia de consultas RAG")

_STOP = frozenset(
    "de la el en y a los las del se que por un una con para es al lo como mas su sus o este esta son "
    "ser fue ha han pero sin sobre entre cuando muy ya tambien si no hay donde cual quien".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")
_ORACIONES = re.compile(r"(?<=[.!?;:])\s+|\n+")


def _normalizar(t: str) -> str:
    nfkd = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def tokenizar(t: str) -> list[str]:
    return [x for x in _TOKEN.findall(_normalizar(t)) if len(x) > 1 and x not in _STOP]


def generar_embedding(texto: str, dims: int = 384) -> list[float]:
    tokens = tokenizar(texto)
    vec = [0.0] * dims

    def acumular(rasgo: str, peso: float) -> None:
        h = int.from_bytes(hashlib.blake2b(rasgo.encode("utf-8"), digest_size=8).digest(), "big")
        vec[h % dims] += peso if (h >> 63) & 1 else -peso

    for i, tok in enumerate(tokens):
        acumular("u:" + tok, 1.0)
        if i + 1 < len(tokens):
            acumular("b:" + tok + " " + tokens[i + 1], 0.5)
        if len(tok) >= 5:
            for j in range(len(tok) - 2):
                acumular("c:" + tok[j:j + 3], 0.15)
    norma = math.sqrt(sum(x * x for x in vec))
    if norma == 0.0:
        vec[0] = 1.0
        return vec
    return [x / norma for x in vec]


def literal_vector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


_SQL_HIBRIDA = text("""
WITH vec AS (
    SELECT t.id, t.sim, ROW_NUMBER() OVER (ORDER BY t.sim DESC) AS rk
    FROM (
        SELECT f.id, 1 - (f.embedding <=> CAST(CAST(:vec AS text) AS vector)) AS sim
        FROM fragmentos_vectoriales f
        JOIN documentos d ON d.id = f.documento_id
        WHERE d.estado = 'INDEXADO'
        ORDER BY f.embedding <=> CAST(CAST(:vec AS text) AS vector)
        LIMIT :cand
    ) t
), lex AS (
    SELECT t.id, t.rango, ROW_NUMBER() OVER (ORDER BY t.rango DESC) AS rk
    FROM (
        SELECT f.id, ts_rank_cd(f.tsv, q.consulta) AS rango
        FROM fragmentos_vectoriales f
        JOIN documentos d ON d.id = f.documento_id
        CROSS JOIN (SELECT plainto_tsquery('spanish', CAST(:q AS text)) AS consulta) q
        WHERE d.estado = 'INDEXADO' AND f.tsv @@ q.consulta
        ORDER BY rango DESC
        LIMIT :cand
    ) t
)
SELECT f.id AS fragmento_id, f.documento_id, d.nombre_archivo, d.creado_en, f.pagina, f.indice_fragmento,
       f.contenido_anonimizado,
       COALESCE(v.sim, 0)::float8 AS similitud,
       COALESCE(l.rango, 0)::float8 AS rango_lexico,
       (CAST(:w_vec AS float8) * COALESCE(1.0 / (CAST(:k AS float8) + v.rk), 0)
        + CAST(:w_lex AS float8) * COALESCE(1.0 / (CAST(:k AS float8) + l.rk), 0))::float8 AS score
FROM fragmentos_vectoriales f
JOIN documentos d ON d.id = f.documento_id
LEFT JOIN vec v ON v.id = f.id
LEFT JOIN lex l ON l.id = f.id
WHERE d.estado = 'INDEXADO' AND (v.id IS NOT NULL OR l.id IS NOT NULL)
ORDER BY score DESC
LIMIT :top_k
""")


async def busqueda_hibrida(session: AsyncSession, settings: Settings, pregunta: str,
                           vec: list[float], top_k: int) -> list[dict[str, Any]]:
    res = await session.execute(_SQL_HIBRIDA, {
        "vec": literal_vector(vec), "q": pregunta, "cand": settings.rag_candidatos,
        "k": settings.rrf_k, "w_vec": settings.peso_vectorial, "w_lex": settings.peso_lexico, "top_k": top_k,
    })
    return [dict(r) for r in res.mappings().all()]


def sintetizar(pregunta: str, fragmentos: list[dict[str, Any]], top_k: int) -> tuple[str, list[dict[str, Any]]]:
    q_tokens = set(tokenizar(pregunta))
    citas: list[dict[str, Any]] = []
    for f in fragmentos:
        mejor, mejor_s = None, 0.0
        for oracion in _ORACIONES.split(f["contenido_anonimizado"]):
            o = oracion.strip()
            if len(o) < 8:
                continue
            s = len(q_tokens & set(tokenizar(o))) / (len(q_tokens) or 1)
            if s > mejor_s:
                mejor, mejor_s = o, s
        if mejor is None:
            continue
        citas.append({
            "documento_id": str(f["documento_id"]),
            "nombre_archivo": f["nombre_archivo"],
            "creado_en": (f["creado_en"].isoformat()
                          if hasattr(f["creado_en"], "isoformat")
                          else str(f["creado_en"])) if f["creado_en"] is not None else None,
            "pagina": f["pagina"],
            "indice_fragmento": f["indice_fragmento"],
            "score": round(float(f["score"]), 6),
            "extracto": mejor[:400],
        })
        if len(citas) >= top_k:
            break
    if not citas:
        return "No se encontró información suficiente en los documentos indexados para responder la consulta.", []
    lineas = [f"- {c['extracto']} [{i}]" for i, c in enumerate(citas[:3], start=1)]
    return "Según los documentos indexados:\n" + "\n".join(lineas), citas


async def registrar_metrica(session: AsyncSession, servicio: str, metrica: str, valor: float, unidad: str,
                            correlation_id: str, costo: float = 0.0, limite: float | None = None,
                            metadatos: dict[str, Any] | None = None) -> None:
    await session.execute(text("""
        INSERT INTO metricas_finops (id, servicio, metrica, valor, unidad, costo_estimado_usd,
                                     limite_capa_gratuita, correlation_id, periodo, metadatos, creado_en)
        VALUES (CAST(:id AS uuid), :servicio, :metrica, CAST(:valor AS numeric), :unidad,
                CAST(:costo AS numeric), CAST(:limite AS numeric), :cid, CURRENT_DATE,
                CAST(:meta AS jsonb), now())
    """), {"id": str(uuid.uuid4()), "servicio": servicio, "metrica": metrica, "valor": valor, "unidad": unidad,
           "costo": costo, "limite": limite, "cid": correlation_id, "meta": json.dumps(metadatos or {})})


async def _cache_get(redis, settings: Settings, clave: str, vec: list[float], top_k: int) -> dict[str, Any] | None:
    try:
        exacto = await redis.get(f"rag:exact:{clave}")
        if exacto:
            return json.loads(exacto)
        await redis.zremrangebyscore("rag:sem:idx", 0, time.time() - settings.cache_ttl_segundos)
        claves = await redis.zrevrange("rag:sem:idx", 0, 49)
        if not claves:
            return None
        valores = await redis.mget(claves)
        for v in valores:
            if not v:
                continue
            entrada = json.loads(v)
            if entrada["k"] != top_k:
                continue
            if sum(a * b for a, b in zip(vec, entrada["emb"])) >= settings.semantic_cache_umbral:
                return entrada["payload"]
    except Exception:
        logger.warning("Caché Redis no disponible; se continúa sin caché")
    return None


async def _cache_set(redis, settings: Settings, clave: str, vec: list[float], top_k: int,
                     payload: dict[str, Any]) -> None:
    try:
        ttl = settings.cache_ttl_segundos
        await redis.set(f"rag:exact:{clave}", json.dumps(payload, ensure_ascii=False), ex=ttl)
        entrada = {"emb": [round(x, 5) for x in vec], "k": top_k, "payload": payload}
        await redis.set(f"rag:sem:{clave}", json.dumps(entrada), ex=ttl)
        await redis.zadd("rag:sem:idx", {f"rag:sem:{clave}": time.time()})
        exceso = await redis.zcard("rag:sem:idx") - settings.semantic_cache_max_entradas
        if exceso > 0:
            await redis.zremrangebyrank("rag:sem:idx", 0, exceso - 1)
    except Exception:
        logger.warning("No fue posible escribir en la caché Redis")


async def consultar(session: AsyncSession, redis, settings: Settings, pregunta: str,
                    top_k: int | None, correlation_id: str) -> dict[str, Any]:
    inicio = time.perf_counter()
    k = top_k or settings.rag_top_k
    anon = anonimizar(pregunta, settings.pii_anonymize_enabled)
    texto_q = anon.texto
    clave = hashlib.sha256(f"{k}|{_normalizar(texto_q)}".encode("utf-8")).hexdigest()
    vec = generar_embedding(texto_q, settings.embedding_dimensions)

    payload = await _cache_get(redis, settings, clave, vec, k)
    cache_hit = payload is not None
    if payload is None:
        fragmentos = await busqueda_hibrida(session, settings, texto_q, vec, k)
        respuesta, citas = sintetizar(texto_q, fragmentos, k)
        payload = {"respuesta": respuesta, "citas": citas, "fragmentos_recuperados": len(fragmentos)}
        await _cache_set(redis, settings, clave, vec, k, payload)

    latencia_ms = int((time.perf_counter() - inicio) * 1000)
    tokens_entrada = len(texto_q.split())
    tokens_salida = len(payload["respuesta"].split())

    await session.execute(text("""
        INSERT INTO auditoria_consultas (id, correlation_id, pregunta_anonimizada, respuesta, citas,
            fragmentos_recuperados, cache_hit, pii_detectada, latencia_ms, proveedor_embedding, proveedor_llm,
            tokens_entrada, tokens_salida, costo_estimado_usd, estado_http, creado_en)
        VALUES (CAST(:id AS uuid), :cid, :preg, :resp, CAST(:citas AS jsonb), :frag, :hit, :pii, :lat,
            :pe, :pl, :te, :ts, 0, 200, now())
    """), {"id": str(uuid.uuid4()), "cid": correlation_id, "preg": texto_q, "resp": payload["respuesta"],
           "citas": json.dumps(payload["citas"], ensure_ascii=False), "frag": payload["fragmentos_recuperados"],
           "hit": cache_hit, "pii": anon.detectada, "lat": latencia_ms, "pe": settings.embedding_provider,
           "pl": settings.llm_provider, "te": tokens_entrada, "ts": tokens_salida})
    await registrar_metrica(session, "api", "consultas_rag", 1, "consultas", correlation_id,
                            metadatos={"cache_hit": cache_hit, "latencia_ms": latencia_ms})
    if not cache_hit:
        await registrar_metrica(session, "embeddings", "embeddings_generados", 1, "vectores", correlation_id,
                                metadatos={"proveedor": settings.embedding_provider})
        await registrar_metrica(session, "llm", "tokens_generados", tokens_salida, "tokens", correlation_id,
                                metadatos={"proveedor": settings.llm_provider})
    await session.commit()

    RAG_CONSULTAS.labels(str(cache_hit).lower()).inc()
    RAG_LATENCIA.observe(latencia_ms / 1000)
    return {
        "respuesta": payload["respuesta"],
        "citas": payload["citas"],
        "fragmentos_recuperados": payload["fragmentos_recuperados"],
        "cache_hit": cache_hit,
        "pii_detectada": anon.detectada,
        "pii_resumen": anon.resumen(),
        "pregunta_anonimizada": texto_q,
        "latencia_ms": latencia_ms,
        "correlation_id": correlation_id,
    }
