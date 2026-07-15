"""Scalable, fail-closed hybrid retrieval over immutable policy-rule versions.

The compatibility mutation functions at the bottom still support legacy
``qa_adjudications`` during a rolling deployment.  New evaluation code uses one
set-based SQL statement for all criteria and transcript chunks, a real relevance
gate, stable criterion IDs, and a structured trace.  Provider/DB failures never
fall back to arbitrary recent rows.
"""
from __future__ import annotations

import json
import re
import time

from .. import config
from ..embeddings.provider import get_provider


class AdjudicationEmbeddingUnavailable(RuntimeError):
    """Семантическую правку нельзя безопасно сохранить без нового embedding."""


class AdjudicationConflict(RuntimeError):
    """Разбор несколько раз изменился между чтением и атомарной записью."""


def _rw_conn():
    return config.connect_rw()


def criteria_with_adjudications(direction_id) -> set:
    """Индексы критериев направления, по которым уже есть разборы — их отдаём на HARD-модель,
    чтобы стандарт из разбора применяла сильная модель (даже если BULK уверенно поставил Correct)."""
    try:
        ro = config.connect_ro(); cur = ro.cursor()
        try:
            cur.execute("""SELECT DISTINCT criterion_idx FROM qa_active_policy_rules
                            WHERE direction_id=%s AND criterion_idx IS NOT NULL
                              AND index_status='ready'""", (direction_id,))
        except Exception:
            cur.close(); ro.close()
            ro = config.connect_ro(); cur = ro.cursor()
            cur.execute("SELECT DISTINCT criterion_idx FROM qa_adjudications WHERE direction_id=%s AND is_active",
                        (direction_id,))
        idx = {r[0] for r in cur.fetchall()}
        cur.close(); ro.close()
        return idx
    except Exception:
        return set()


def _vec(v):
    """Список float → текстовый формат pgvector '[1,2,3]' (или None)."""
    return "[" + ",".join(str(float(x)) for x in v) + "]" if v else None


def _embed(text):
    try:
        return get_provider().embed_document([text])[0]
    except Exception:
        return None  # разбор сохраняем и без вектора


# Vertex text-multilingual-embedding-002 принимает ~2048 токенов НА ИНСТАНС и молча
# обрезает хвост: весь звонок одним вектором физически не влезает — эмбеддинг строился бы
# только по первым минутам. Поэтому транскрипт эмбеддится КУСКАМИ (одним HTTP-вызовом),
# а retrieval берёт максимум близости по кускам: правило находится, если его ситуация
# встречается в любой части звонка.
_EMBED_CHUNK_CHARS = config.EMBED_CHUNK_CHARS
_EMBED_CHUNK_OVERLAP = config.EMBED_CHUNK_OVERLAP
_EMBED_MAX_CHUNKS = config.EMBED_MAX_CHUNKS


def _uniform_indices(size: int, limit: int) -> list[int]:
    if size <= limit:
        return list(range(size))
    if limit <= 1:
        return [0]
    return sorted({round(i * (size - 1) / (limit - 1)) for i in range(limit)})


def _chunk_manifest(text: str) -> list[dict]:
    """Overlapping character windows with source offsets and uniform sampling."""
    value = str(text or "")
    if not value:
        return []
    window = max(256, int(_EMBED_CHUNK_CHARS))
    overlap = max(0, min(int(_EMBED_CHUNK_OVERLAP), window - 1))
    step = window - overlap
    windows = []
    start = 0
    source_index = 0
    while start < len(value):
        end = min(len(value), start + window)
        # Prefer a nearby natural boundary without ever dropping text from the
        # represented source range.  A long single utterance is still sliced.
        if end < len(value):
            boundary = max(value.rfind("\n", start + window // 2, end),
                           value.rfind(" ", start + window // 2, end))
            if boundary > start:
                end = boundary + 1
        chunk = value[start:end].strip()
        if chunk:
            windows.append({"source_index": source_index, "start": start,
                            "end": end, "text": chunk, "chars": len(chunk)})
            source_index += 1
        if end >= len(value):
            break
        next_start = max(start + 1, end - overlap)
        start = next_start
    chosen = [windows[index] for index in _uniform_indices(len(windows), _EMBED_MAX_CHUNKS)]
    for chunk_idx, item in enumerate(chosen):
        item["chunk_idx"] = chunk_idx
    return chosen


def _chunk_for_embedding(text: str) -> list[str]:
    """Compatibility helper returning the text of production chunk windows."""
    return [item["text"] for item in _chunk_manifest(text)]


def embed_query_chunks(text, *, return_batch: bool = False):
    """Embed the whole call once with the explicit query role.

    ``return_batch=False`` preserves the legacy list API.  The production batch
    form distinguishes an empty/no-match input from provider degradation and
    carries safe chunk offsets for the retrieval trace.
    """
    started = time.perf_counter()
    chunks = _chunk_manifest(text)
    if not chunks:
        batch = {"status": "no_match", "vectors": [], "provider": None,
                 "chunks": [], "transcript_chars": len(str(text or "")),
                 "latency_ms": 0, "error": None}
        return batch if return_batch else []
    try:
        provider = get_provider()
        vectors = provider.embed_query([item["text"] for item in chunks])
        batch = {
            "status": "ok", "vectors": vectors, "provider": provider.metadata,
            "chunks": [{key: item[key] for key in
                        ("chunk_idx", "source_index", "start", "end", "chars")}
                       for item in chunks],
            "transcript_chars": len(str(text or "")),
            "latency_ms": round((time.perf_counter() - started) * 1000), "error": None,
        }
    except Exception as exc:
        batch = {
            "status": "degraded", "vectors": [], "provider": None,
            "chunks": [{key: item[key] for key in
                        ("chunk_idx", "source_index", "start", "end", "chars")}
                       for item in chunks],
            "transcript_chars": len(str(text or "")),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }
    return batch if return_batch else batch["vectors"]


def embed_document_text(text: str, *, strict: bool = True) -> dict:
    """Embed a rule with the document role and expose its index identity."""
    try:
        provider = get_provider()
        vector = provider.embed_document([str(text or "")])[0]
        return {"vector": vector, "provider": provider.metadata}
    except Exception:
        if strict:
            raise
        return {"vector": None, "provider": None}


def bump_use_count(ids):
    """+1 к use_count подтянутых в оценку разборов — в «Базе разборов» видно, какие
    правила реально работают. Best-effort: без RW (локальная разработка) тихо пропускаем."""
    legacy_ids = [value for value in ids if isinstance(value, int) or str(value).isdigit()]
    if not legacy_ids:
        return
    try:
        with config.connect_rw() as c, c.cursor() as cur:
            cur.execute("UPDATE qa_adjudications SET use_count = use_count + 1 WHERE id = ANY(%s)",
                        ([int(value) for value in legacy_ids],))
        c.close()
    except Exception:
        pass


def _adjudication_embed_text(criterion_name, situation, excerpt, reason, not_covered) -> str:
    """Текст, по которому разбор ищется семантически. В него входят и границы (not_covered):
    прецедент должен находиться и по звонкам с нарушением, которое правило НЕ оправдывает."""
    return ". ".join(p for p in (criterion_name, situation, excerpt, reason, not_covered) if p)


def save_adjudication(*, direction_id, criterion_idx, criterion_name, call_id,
                      excerpt, ai_verdict, correct_verdict, reason,
                      not_covered=None, situation=None, situation_tag=None, created_by=None) -> int:
    """Авто-сохранение разбора человека (+ embedding). Вызывается из экшена ревью."""
    vec = _embed(_adjudication_embed_text(criterion_name, situation, excerpt, reason, not_covered))
    with _rw_conn() as c, c.cursor() as cur:
        cur.execute(
            """INSERT INTO qa_adjudications
               (direction_id, criterion_idx, criterion_name, call_id, excerpt,
                ai_verdict, correct_verdict, reason, not_covered, situation, situation_tag, embedding, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s) RETURNING id""",
            (direction_id, criterion_idx, criterion_name, call_id, excerpt,
             ai_verdict, correct_verdict, reason, not_covered or None, situation or None,
             situation_tag, _vec(vec), created_by),
        )
        return cur.fetchone()[0]


_RETRIEVE_COLS = ["id", "criterion_name", "excerpt", "ai_verdict", "correct_verdict",
                  "reason", "not_covered", "situation", "sim"]


def _retrieve_one(cur, direction_id, criterion_idx, qvec, k) -> list[dict]:
    if qvec:
        cur.execute(
            """SELECT id, criterion_name, excerpt, ai_verdict, correct_verdict, reason, not_covered, situation,
                      1 - (embedding <=> %s::vector) AS sim
                 FROM qa_adjudications
                 WHERE direction_id=%s AND criterion_idx=%s AND embedding IS NOT NULL AND is_active
                ORDER BY embedding <=> %s::vector LIMIT %s""",
            (_vec(qvec), direction_id, criterion_idx, _vec(qvec), k))
    else:
        # Fail closed: recency is not relevance.  An embedding outage/empty query
        # must produce "no applicable rule", never arbitrary latest records.
        return []
    return [dict(zip(_RETRIEVE_COLS, r)) for r in cur.fetchall()]


# Поля разбора, которые можно править из админки. Ключи попадают в SET напрямую,
# поэтому список закрыт и проверяется и здесь, и в api (защита в глубину).
EDITABLE_ADJ_FIELDS = ("correct_verdict", "reason", "situation", "not_covered", "situation_tag")
_ADJ_EMBED_SOURCE_FIELDS = ("criterion_name", "situation", "excerpt", "reason", "not_covered")
_ADJ_SNAPSHOT_FIELDS = _ADJ_EMBED_SOURCE_FIELDS + ("correct_verdict", "situation_tag")


def _adjudication_source(adj_id: int) -> dict | None:
    """Короткий снимок текста без блокировки: внешний embedding считаем уже после закрытия БД."""
    conn = config.connect_rw()
    try:
        with conn.cursor() as cur:
            cur.execute("SET client_encoding TO 'UTF8'")
            cur.execute(f"""SELECT {', '.join(_ADJ_SNAPSHOT_FIELDS)}
                              FROM qa_adjudications
                             WHERE id=%s AND is_active""", (adj_id,))
            row = cur.fetchone()
            return dict(zip(_ADJ_SNAPSHOT_FIELDS, row)) if row else None
    finally:
        conn.close()


def _write_adjudication(adj_id: int, fields: dict, *, vec=None, source: dict | None = None) -> bool:
    """Атомарная короткая запись. source включает optimistic check от параллельной правки."""
    sets = ", ".join(f"{key}=%s" for key in fields)
    params = list(fields.values())
    if vec is not None:
        sets += ", embedding=%s::vector"
        params.append(_vec(vec))
    where = "id=%s AND is_active"
    params.append(adj_id)
    if source is not None:
        where += " AND " + " AND ".join(
            f"{key} IS NOT DISTINCT FROM %s" for key in _ADJ_SNAPSHOT_FIELDS)
        params.extend(source[key] for key in _ADJ_SNAPSHOT_FIELDS)

    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET client_encoding TO 'UTF8'")
            cur.execute(f"UPDATE qa_adjudications SET {sets} WHERE {where} RETURNING id", params)
            return cur.fetchone() is not None
    finally:
        conn.close()


def update_adjudication(adj_id: int, fields: dict) -> bool:
    """Правка разбора (супер-админ). fields — уже провалидированный патч
    (см. api._clean_adjudication_patch). Текст правила меняется → embedding
    пересчитывается, иначе поиск продолжал бы находить разбор по старому смыслу.
    False — разбора нет."""
    fields = {k: v for k, v in fields.items() if k in EDITABLE_ADJ_FIELDS}
    if not fields:
        raise ValueError("нет полей для изменения")
    # Optimistic snapshot сохраняет соответствие embedding и всех редактируемых полей,
    # но не держит row lock во время внешнего Vertex-запроса (до 60 секунд).
    source = _adjudication_source(adj_id)
    if source is None:
        return False
    merged = {**source, **{key: value for key, value in fields.items() if key in source}}
    semantic_changed = any(
        merged[key] != source[key] for key in _ADJ_EMBED_SOURCE_FIELDS)
    vec = None
    if semantic_changed:
        vec = _embed(_adjudication_embed_text(
            merged["criterion_name"], merged["situation"], merged["excerpt"],
            merged["reason"], merged["not_covered"],
        ))
        if vec is None:
            raise AdjudicationEmbeddingUnavailable(
                "не удалось пересчитать embedding; изменения не сохранены, повторите позже")
    if _write_adjudication(adj_id, fields, vec=vec, source=source):
        return True
    raise AdjudicationConflict("разбор был изменён параллельно; повторите запрос")


def delete_adjudication(adj_id: int) -> bool:
    """Отключение разбора (супер-админ): из RAG он исчезает сразу, но остаётся
    историческим следом человеческой корректировки для trust-метрик. False — разбора нет."""
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""UPDATE qa_adjudications SET is_active=FALSE
                            WHERE id=%s AND is_active RETURNING id""", (adj_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()


_LEXICAL_STOPWORDS = {
    "который", "которая", "которые", "этого", "этот", "были", "было", "есть",
    "если", "только", "после", "перед", "потому", "также", "клиент", "оператор",
    "звонок", "звонка", "менеджер", "сказал", "сказала", "және", "үшін", "деген",
    "болды", "керек", "клиентке", "оператордың",
}


def _lexical_query(text: str, *, limit: int = 72) -> str:
    """Build a bounded OR tsquery from distinctive transcript terms."""
    terms = {token for token in re.findall(r"[^\W\d_]{4,}", str(text or "").casefold(), re.UNICODE)
             if token not in _LEXICAL_STOPWORDS}
    selected = sorted(terms, key=lambda item: (-len(item), item))[:limit]
    # Tokens come from a letters-only regexp; quoting is still explicit for a
    # predictable tsquery and to avoid treating user text as syntax.
    return " | ".join("'" + item.replace("'", "''") + "'" for item in selected)


_BATCH_RETRIEVAL_SQL = """
WITH criteria_input AS (
    SELECT criterion_id, criterion_idx
      FROM jsonb_to_recordset(%s::jsonb)
           AS x(criterion_id text, criterion_idx integer)
),
query_vectors AS (
    SELECT (ordinality - 1)::integer AS chunk_idx, raw_vector::vector AS embedding
      FROM unnest(%s::text[]) WITH ORDINALITY AS q(raw_vector, ordinality)
),
rules AS NOT MATERIALIZED (
    SELECT rule_id,rule_version_id,source_type,legacy_adjudication_id,direction_id,
           criterion_id,criterion_idx,criterion_name,situation,excerpt,correct_verdict,
           reason,not_covered,rule_version,content_hash,embedding,
           embedding_provider,embedding_model,embedding_dim,index_status,
           embedding_config_hash,search_document
      FROM qa_active_policy_rules
     WHERE %s::bigint IS NULL
    UNION ALL
    SELECT rule_id,rule_version_id,source_type,legacy_adjudication_id,direction_id,
           criterion_id,criterion_idx,criterion_name,situation,excerpt,correct_verdict,
           reason,not_covered,rule_version,content_hash,embedding,
           embedding_provider,embedding_model,embedding_dim,index_status,
           embedding_config_hash,search_document
      FROM qa_snapshot_policy_rules
     WHERE %s::bigint IS NOT NULL AND snapshot_id=%s::bigint
),
dense_chunk_candidates AS (
    SELECT ci.criterion_id AS requested_criterion_id,
           ci.criterion_idx AS requested_criterion_idx,
           q.chunk_idx, nearest.*
      FROM criteria_input ci
      CROSS JOIN query_vectors q
      CROSS JOIN LATERAL (
          SELECT r.rule_id,r.rule_version_id,r.source_type,r.legacy_adjudication_id,
                 r.criterion_name,r.situation,r.excerpt,r.correct_verdict,
                 r.reason,r.not_covered,r.rule_version,r.content_hash,r.search_document,
                 1 - (/*DISTANCE*/) AS similarity
            FROM rules r
           WHERE r.direction_id=%s
             AND (r.criterion_id=ci.criterion_id OR
                  (r.source_type='legacy' AND r.criterion_id IS NULL
                   AND r.criterion_idx=ci.criterion_idx))
             AND r.index_status='ready' AND r.embedding IS NOT NULL
             AND r.embedding_provider=%s AND r.embedding_model=%s
             AND r.embedding_dim=%s AND r.embedding_config_hash=%s
           ORDER BY /*DISTANCE*/, r.rule_id
           LIMIT %s
      ) nearest
),
dense_best AS (
    SELECT DISTINCT ON (requested_criterion_id,rule_id) *
      FROM dense_chunk_candidates
     ORDER BY requested_criterion_id,rule_id,similarity DESC,chunk_idx
),
dense_ranked AS (
    SELECT d.*, row_number() OVER (
               PARTITION BY requested_criterion_id
               ORDER BY similarity DESC, rule_id) AS dense_rank
      FROM dense_best d
),
lexical_ranked AS (
    SELECT e.requested_criterion_id, e.rule_id,
           ts_rank_cd(e.search_document, to_tsquery('simple', %s)) AS lexical_score,
           row_number() OVER (
               PARTITION BY e.requested_criterion_id
               ORDER BY ts_rank_cd(e.search_document, to_tsquery('simple', %s)) DESC,
                        e.rule_id) AS lexical_rank
      FROM dense_best e
     WHERE %s <> '' AND e.search_document @@ to_tsquery('simple', %s)
),
fused AS (
    SELECT d.*,
           l.lexical_rank, l.lexical_score,
           (1.0 / (60 + d.dense_rank))
             + CASE WHEN l.lexical_rank IS NULL THEN 0
                    ELSE 1.0 / (60 + l.lexical_rank) END AS fused_score
      FROM dense_ranked d
      LEFT JOIN lexical_ranked l
        ON l.requested_criterion_id=d.requested_criterion_id AND l.rule_id=d.rule_id
),
ranked AS (
    SELECT f.*, row_number() OVER (
               PARTITION BY requested_criterion_id
               ORDER BY fused_score DESC, similarity DESC, rule_id) AS final_rank
      FROM fused f
)
SELECT requested_criterion_id,requested_criterion_idx,rule_id,rule_version_id,
       source_type,legacy_adjudication_id,criterion_name,situation,excerpt,
       correct_verdict,reason,not_covered,rule_version,content_hash,
       chunk_idx,similarity,dense_rank,lexical_rank,lexical_score,fused_score,final_rank
  FROM ranked
 WHERE final_rank <= %s
 ORDER BY requested_criterion_idx, final_rank
"""


_LEGACY_BATCH_RETRIEVAL_SQL = """
WITH criteria_input AS (
    SELECT criterion_id, criterion_idx
      FROM jsonb_to_recordset(%s::jsonb)
           AS x(criterion_id text, criterion_idx integer)
),
query_vectors AS (
    SELECT (ordinality - 1)::integer AS chunk_idx, raw_vector::vector AS embedding
      FROM unnest(%s::text[]) WITH ORDINALITY AS q(raw_vector, ordinality)
),
best AS (
    SELECT ci.criterion_id,ci.criterion_idx,a.id,a.criterion_name,a.situation,a.excerpt,
           a.correct_verdict,a.reason,a.not_covered,coalesce(a.rule_version,1),
           a.content_hash::text,q.chunk_idx,
           1-(a.embedding <=> q.embedding) AS similarity,
           row_number() OVER (PARTITION BY ci.criterion_id,a.id
                              ORDER BY a.embedding <=> q.embedding) AS chunk_rank
      FROM criteria_input ci
      JOIN qa_adjudications a ON a.direction_id=%s AND a.is_active
       AND (a.criterion_id=ci.criterion_id OR
            (a.criterion_id IS NULL AND a.criterion_idx=ci.criterion_idx))
      CROSS JOIN query_vectors q
     WHERE a.embedding IS NOT NULL
), ranked AS (
    SELECT *,row_number() OVER (PARTITION BY criterion_id ORDER BY similarity DESC,id) AS final_rank
      FROM best WHERE chunk_rank=1
)
SELECT criterion_id,criterion_idx,('legacy:'||id),('legacy:'||id||':v'||coalesce),
       'legacy',id,criterion_name,situation,excerpt,correct_verdict,reason,not_covered,
       coalesce,content_hash,chunk_idx,similarity,final_rank,NULL,NULL,
       (1.0/(60+final_rank)),final_rank
  FROM ranked WHERE final_rank<=%s ORDER BY criterion_idx,final_rank
"""


_BATCH_COLUMNS = (
    "criterion_id", "criterion_idx", "rule_id", "rule_version_id", "source_type",
    "legacy_adjudication_id", "criterion_name", "situation", "excerpt",
    "correct_verdict", "reason", "not_covered", "rule_version", "content_hash",
    "best_chunk_idx", "similarity", "dense_rank", "lexical_rank", "lexical_score",
    "fused_score", "rank",
)


def _batch_sql_for_dim(dim: int) -> str:
    """Use a fixed typmod where an expression HNSW index is provisioned."""
    dim = int(dim)
    if dim in {384, 768}:
        distance = f"(r.embedding::vector({dim})) <=> (q.embedding::vector({dim}))"
    else:
        # Correct fail-safe path for an explicitly configured uncommon dimension;
        # operators should add the matching expression HNSW index before canary.
        distance = "r.embedding <=> q.embedding"
    return _BATCH_RETRIEVAL_SQL.replace("/*DISTANCE*/", distance)


def _is_rolling_schema_error(exc: Exception) -> bool:
    return getattr(exc, "pgcode", None) in {"42P01", "42703", "42883"}


def _empty_batch_result(criteria: list[dict], *, status: str, query_batch: dict,
                        config_payload: dict, error: str | None = None) -> dict:
    return {
        "status": status,
        "hits_by_criterion": {item["idx"]: [] for item in criteria},
        "trace": {
            "status": status, "config": config_payload,
            "embedding": query_batch.get("provider"),
            "query": {"chunks": query_batch.get("chunks") or [],
                      "transcript_chars": query_batch.get("transcript_chars", 0),
                      "embedding_ms": query_batch.get("latency_ms", 0),
                      "embedding_requests": 1 if query_batch.get("chunks") else 0,
                      "sql_queries": 0},
            "criteria": [{"criterion_id": item.get("criterion_id"), "criterion_idx": item["idx"]}
                         for item in criteria],
            "candidates": [], "errors": [error] if error else [], "latency_ms": 0,
        },
    }


def retrieve_for_criteria_batch(*, direction_id: int, criteria: list[dict],
                                query_batch: dict, k: int | None = None,
                                min_similarity: float | None = None,
                                knowledge_snapshot_id: int | None = None,
                                query_text: str | None = None) -> dict:
    """Retrieve all criteria/chunks with one SQL round trip and return an audit trace."""
    started = time.perf_counter()
    criteria = [item for item in (criteria or []) if item.get("criterion_id") and item.get("idx") is not None]
    top_k = max(1, int(k or config.RETRIEVAL_TOP_K))
    threshold = float(config.RETRIEVAL_MIN_SIMILARITY if min_similarity is None else min_similarity)
    candidate_limit = max(top_k, top_k * max(1, int(config.RETRIEVAL_CANDIDATE_MULTIPLIER)))
    cfg = {
        "pipeline": "hybrid-set-v2", "top_k": top_k,
        "min_similarity": threshold,
        "candidate_multiplier": int(config.RETRIEVAL_CANDIDATE_MULTIPLIER),
        "lexical_min_score": float(getattr(config, "RETRIEVAL_LEXICAL_MIN_SCORE", 0.05)),
        "knowledge_snapshot_id": knowledge_snapshot_id,
        "embedding_config_hash": (query_batch.get("provider") or {}).get("config_hash"),
    }
    if not criteria:
        return _empty_batch_result(criteria, status="no_match", query_batch=query_batch,
                                   config_payload=cfg)
    if query_batch.get("status") == "degraded":
        return _empty_batch_result(
            criteria, status="degraded", query_batch=query_batch, config_payload=cfg,
            error=query_batch.get("error") or "embedding provider unavailable")
    vectors = query_batch.get("vectors") or []
    provider = query_batch.get("provider") or {}
    if not vectors:
        return _empty_batch_result(criteria, status="no_match", query_batch=query_batch,
                                   config_payload=cfg)
    if (not provider.get("provider") or not provider.get("model") or not provider.get("dim")
            or not provider.get("config_hash")):
        return _empty_batch_result(criteria, status="degraded", query_batch=query_batch,
                                   config_payload=cfg, error="embedding identity is missing")
    if any(len(vector) != int(provider["dim"]) for vector in vectors):
        return _empty_batch_result(criteria, status="degraded", query_batch=query_batch,
                                   config_payload=cfg, error="query embedding dimension mismatch")

    criterion_payload = [{"criterion_id": str(item["criterion_id"]),
                          "criterion_idx": int(item["idx"])} for item in criteria]
    vector_payload = [_vec(vector) for vector in vectors]
    lexical = _lexical_query(query_text or "")
    sql_queries = 0
    rows = []
    error = None
    try:
        conn = config.connect_ro(); cur = conn.cursor()
        try:
            sql_queries += 1
            cur.execute(
                _batch_sql_for_dim(int(provider["dim"])),
                (json.dumps(criterion_payload, ensure_ascii=False), vector_payload,
                 knowledge_snapshot_id, knowledge_snapshot_id, knowledge_snapshot_id,
                 int(direction_id), provider["provider"], provider["model"], int(provider["dim"]),
                 provider["config_hash"], candidate_limit,
                 lexical, lexical, lexical, lexical, candidate_limit),
            )
            rows = cur.fetchall()
        except Exception as exc:
            # A partial schema must fail closed.  Falling back to legacy
            # ``is_active`` rows would bypass evidence quarantine and the exact
            # provider/model/config contract.
            raise
        finally:
            cur.close(); conn.close()
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if error:
        result = _empty_batch_result(criteria, status="degraded", query_batch=query_batch,
                                     config_payload=cfg, error=error)
        result["trace"]["latency_ms"] = elapsed_ms
        result["trace"]["query"]["sql_queries"] = sql_queries
        return result

    by_id = {str(item["criterion_id"]): item for item in criteria}
    candidates = []
    hits = {item["idx"]: [] for item in criteria}
    selected_count = {}
    lexical_min = float(getattr(config, "RETRIEVAL_LEXICAL_MIN_SCORE", 0.05))
    lexical_margin = float(getattr(config, "RETRIEVAL_LEXICAL_DENSE_MARGIN", 0.08))
    for raw in rows:
        item = dict(zip(_BATCH_COLUMNS, raw))
        criterion = by_id.get(str(item["criterion_id"]))
        if not criterion:
            continue
        similarity = float(item["similarity"]) if item["similarity"] is not None else None
        lexical_score = float(item["lexical_score"]) if item["lexical_score"] is not None else None
        qualifies = similarity is not None and similarity >= threshold
        if (not qualifies and similarity is not None and lexical_score is not None
                and lexical_score >= lexical_min and similarity >= threshold - lexical_margin):
            qualifies = True
        used = selected_count.get(item["criterion_id"], 0)
        included = qualifies and used < top_k
        if included:
            selected_count[item["criterion_id"]] = used + 1
        reject_reason = None if included else ("top_k_exceeded" if qualifies else "below_threshold")
        candidate = {
            **item,
            "criterion_idx": int(criterion["idx"]),
            "similarity": similarity,
            "lexical_score": lexical_score,
            "fused_score": float(item["fused_score"]) if item["fused_score"] is not None else None,
            "included": included, "reject_reason": reject_reason,
        }
        candidates.append(candidate)
        if included:
            hit = {
                "id": item["legacy_adjudication_id"] if item["source_type"] == "legacy" else item["rule_id"],
                "rule_id": item["rule_id"], "rule_version_id": item["rule_version_id"],
                "source_type": item["source_type"], "criterion_name": item["criterion_name"],
                "situation": item["situation"], "excerpt": item["excerpt"],
                "correct_verdict": item["correct_verdict"], "reason": item["reason"],
                "not_covered": item["not_covered"], "sim": similarity,
                "similarity": similarity, "rank": int(item["rank"]),
                "content_hash": item["content_hash"], "rule_version": item["rule_version"],
            }
            hits[int(criterion["idx"])].append(hit)
    status = "ok" if any(hits.values()) else "no_match"
    trace = {
        "status": status, "config": cfg, "embedding": provider,
        "query": {"chunks": query_batch.get("chunks") or [],
                  "transcript_chars": query_batch.get("transcript_chars", 0),
                  "embedding_ms": query_batch.get("latency_ms", 0),
                  "embedding_requests": 1, "sql_queries": sql_queries},
        "criteria": [{"criterion_id": item["criterion_id"], "criterion_idx": item["idx"],
                      "retrieved_count": sum(1 for c in candidates if c["criterion_id"] == item["criterion_id"]),
                      "included_count": len(hits[item["idx"]])} for item in criteria],
        "candidates": candidates, "errors": [], "latency_ms": elapsed_ms,
    }
    return {"status": status, "hits_by_criterion": hits, "trace": trace}


def retrieve(*, direction_id, criterion_idx, query_text=None, query_vector=None, k=None) -> list[dict]:
    """Разборы по критерию. С query_text/query_vector — ранжируем по косинусной близости (pgvector),
    иначе возвращаем пусто. Ограниченный список → промпт не растёт с базой."""
    k = k or config.RETRIEVAL_TOP_K
    ro = config.connect_ro(); cur = ro.cursor()
    try:
        qvec = query_vector if query_vector is not None else (_embed(query_text) if query_text else None)
        return _retrieve_one(cur, direction_id, criterion_idx, qvec, k)
    finally:
        cur.close(); ro.close()


def _retrieve_multi(cur, direction_id, criterion_idx, query_vectors, k) -> list[dict]:
    """Top-K по максимуму близости среди кусков транскрипта (см. _chunk_for_embedding)."""
    best = {}
    for qv in query_vectors:
        for h in _retrieve_one(cur, direction_id, criterion_idx, qv, k):
            prev = best.get(h["id"])
            if prev is None or (h["sim"] or 0) > (prev["sim"] or 0):
                best[h["id"]] = h
    return sorted(best.values(), key=lambda h: -(h["sim"] or 0))[:k]


def retrieve_for_criteria(*, direction_id, criterion_idxs, query_vectors=None, k=None) -> dict:
    """Разборы сразу по нескольким критериям ОДНИМ подключением: оценка звонка делала
    по TLS-хендшейку к Postgres на каждый критерий (~15 на звонок) — теперь один.
    query_vectors — эмбеддинги кусков транскрипта; пусто → нет совпадений.
    Возвращает {criterion_idx: [hits]}."""
    k = k or config.RETRIEVAL_TOP_K
    out = {idx: [] for idx in criterion_idxs}
    if not criterion_idxs:
        return out
    ro = config.connect_ro(); cur = ro.cursor()
    try:
        for idx in criterion_idxs:
            if query_vectors:
                out[idx] = _retrieve_multi(cur, direction_id, idx, query_vectors, k)
            else:
                out[idx] = _retrieve_one(cur, direction_id, idx, None, k)
    finally:
        cur.close(); ro.close()
    return out
