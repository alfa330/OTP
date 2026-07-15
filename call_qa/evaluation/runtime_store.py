"""Persistence boundary for immutable ASR/evaluation artifacts and RAG traces."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
import uuid

from psycopg2.extras import Json

from .. import config
from .fingerprint import content_hash


class RuntimeSchemaUnavailable(RuntimeError):
    pass


_AUTO_COST = object()


def is_schema_compat_error(exc: Exception) -> bool:
    return getattr(exc, "pgcode", None) in {"42P01", "42703", "42883"}


def audio_fingerprint(*, call_id: int, audio_path: str,
                      generation=None, etag=None, size=None, md5_hash=None) -> str:
    """Hash the immutable object identity, not a signed URL or local temp path."""
    return content_hash({
        "version": 1, "call_id": int(call_id), "audio_path": str(audio_path),
        "generation": str(generation) if generation is not None else None,
        "etag": etag, "size": int(size) if size is not None else None,
        "md5_hash": md5_hash,
    })


def asr_config() -> dict:
    return {
        "provider": "soniox", "model": config.SONIOX_MODEL,
        "language_hints": list(config.SONIOX_LANGS),
        "language_identification": True, "speaker_diarization": True,
        "assembler_version": 2,
    }


@contextmanager
def distributed_call_lock(call_id: int):
    """Cross-worker primary-DB lock; yields False so writers can fail closed."""
    conn = cur = None
    acquired = False
    try:
        # Advisory locks must live on the same primary that stores runs/cases;
        # a read replica would provide a different lock namespace.
        conn = config.connect_rw()
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_lock(%s,%s)", (71623, int(call_id)))
        acquired = True
    except Exception as exc:
        logging.warning("ai-qa: distributed call lock unavailable for %s: %s", call_id, exc)
    try:
        yield acquired
    finally:
        if acquired and cur is not None:
            try:
                cur.execute("SELECT pg_advisory_unlock(%s,%s)", (71623, int(call_id)))
            except Exception:
                logging.exception("ai-qa: failed to release distributed lock for %s", call_id)
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def get_transcript(*, call_id: int, audio_fingerprint_value: str,
                   asr_provider: str, asr_model: str, asr_config_hash: str) -> dict | None:
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id,transcript_hash,transcript_text,segments,tokens,payload,languages,
                          asr_mean_conf,asr_low_spans,duration_ms,created_at
                     FROM ai_transcript_cache
                    WHERE call_id=%s AND audio_fingerprint=%s AND asr_provider=%s
                      AND asr_model=%s AND asr_config_hash=%s
                    ORDER BY created_at DESC LIMIT 1""",
                (int(call_id), audio_fingerprint_value, asr_provider, asr_model, asr_config_hash),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": int(row[0]), "transcript_hash": row[1], "text": row[2],
                "segments": row[3] or [], "tokens": row[4] or [], "payload": row[5] or {},
                "languages": row[6] or {}, "mean_conf": row[7],
                "low_conf_spans": row[8] or [], "duration_ms": row[9], "created_at": row[10],
            }
    except Exception as exc:
        if is_schema_compat_error(exc):
            raise RuntimeSchemaUnavailable("ai_transcript_cache schema is unavailable") from exc
        raise
    finally:
        conn.close()


def put_transcript(*, call_id: int, audio_fingerprint_value: str,
                   asr_provider: str, asr_model: str, asr_config_hash: str,
                   transcript_hash: str, text: str, segments: list,
                   tokens: list | None, payload: dict | None, languages: dict | None,
                   mean_conf=None, low_conf_spans=None, duration_ms=None) -> int:
    """Insert once; a racing worker reuses the winner without mutating history."""
    conn = config.connect_rw()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ai_transcript_cache
                           (call_id,audio_fingerprint,asr_provider,asr_model,asr_config_hash,
                            transcript_hash,transcript_text,segments,tokens,payload,languages,
                            asr_mean_conf,asr_low_spans,duration_ms)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (call_id,audio_fingerprint,asr_provider,asr_model,asr_config_hash)
                         DO NOTHING RETURNING id""",
                    (int(call_id), audio_fingerprint_value, asr_provider, asr_model,
                     asr_config_hash, transcript_hash, text, Json(segments or []),
                     Json(tokens) if tokens is not None else None, Json(payload or {}),
                     Json(languages or {}), mean_conf, Json(low_conf_spans or []), duration_ms),
                )
                row = cur.fetchone()
                if row:
                    return int(row[0])
                cur.execute(
                    """SELECT id FROM ai_transcript_cache
                        WHERE call_id=%s AND audio_fingerprint=%s AND asr_provider=%s
                          AND asr_model=%s AND asr_config_hash=%s""",
                    (int(call_id), audio_fingerprint_value, asr_provider, asr_model,
                     asr_config_hash),
                )
                return int(cur.fetchone()[0])
    except Exception as exc:
        if is_schema_compat_error(exc):
            raise RuntimeSchemaUnavailable("ai_transcript_cache schema is unavailable") from exc
        raise
    finally:
        conn.close()


def get_transcript_by_id(transcript_cache_id: int) -> dict | None:
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id,transcript_hash,transcript_text,segments,tokens,payload,languages,
                          asr_mean_conf,asr_low_spans,duration_ms,created_at
                     FROM ai_transcript_cache WHERE id=%s""",
                (int(transcript_cache_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"id": int(row[0]), "transcript_hash": row[1], "text": row[2],
                    "segments": row[3] or [], "tokens": row[4] or [],
                    "payload": row[5] or {}, "languages": row[6] or {},
                    "mean_conf": row[7], "low_conf_spans": row[8] or [],
                    "duration_ms": row[9], "created_at": row[10]}
    finally:
        conn.close()


def get_adjudication_source(evaluation_run_id: str) -> dict | None:
    """Load the immutable evaluation, transcript and scale shown to a reviewer.

    The compatibility ``ai_review_cache`` is deliberately not consulted here:
    it is a mutable pointer and may already refer to a different force/batch
    run by the time the reviewer submits a correction.
    """
    run_id = _uuid_or_none(evaluation_run_id)
    if not run_id:
        return None
    # Validation runs under a primary advisory lock and must not observe a
    # lagging replica that has not seen a newer run/review yet.
    conn = config.connect_rw()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT e.id::text,e.call_id,e.direction_id,e.transcript_cache_id,
                          e.transcript_hash::text,e.evaluation_fingerprint::text,
                          e.scale_revision_id,e.status,e.run_kind,e.model,e.payload,
                          e.per_criterion,e.created_at,
                          t.id,t.transcript_hash::text,t.transcript_text,t.segments,
                          s.id,s.direction_id,s.content_hash::text,
                          NOT EXISTS (
                              SELECT 1 FROM ai_evaluation_runs newer
                               WHERE newer.call_id=e.call_id
                                 AND newer.status='succeeded'
                                 AND newer.run_kind = ANY(%s)
                                 AND (newer.created_at,newer.id::text) >
                                     (e.created_at,e.id::text)
                          ) AS is_latest,m.review_outcome
                     FROM ai_evaluation_runs e
                     LEFT JOIN ai_transcript_cache t ON t.id=e.transcript_cache_id
                     LEFT JOIN qa_scale_revisions s ON s.id=e.scale_revision_id
                     LEFT JOIN ai_evaluation_meta m
                       ON m.call_id=e.call_id AND m.model=e.model
                    WHERE e.id=%s""",
                (["standard", "force", "batch"], run_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            scale_criteria = []
            if row[17] is not None:
                cur.execute(
                    """SELECT criterion_id,criterion_idx,criterion_name,description,
                              weight,is_critical,deficiency,eval_source,metadata
                         FROM qa_scale_revision_criteria
                        WHERE scale_revision_id=%s
                        ORDER BY criterion_idx""",
                    (int(row[17]),),
                )
                scale_criteria = [{
                    "criterion_id": item[0], "criterion_idx": int(item[1]),
                    "criterion_name": item[2], "description": item[3],
                    "weight": item[4], "is_critical": bool(item[5]),
                    "deficiency": item[6], "eval_source": item[7],
                    "metadata": item[8] or {},
                } for item in cur.fetchall()]
            return {
                "id": row[0], "call_id": int(row[1]), "direction_id": int(row[2]),
                "transcript_cache_id": row[3], "transcript_hash": row[4],
                "evaluation_fingerprint": row[5], "scale_revision_id": row[6],
                "status": row[7], "run_kind": row[8], "model": row[9],
                "payload": row[10] or {}, "per_criterion": row[11] or [],
                "created_at": row[12], "is_latest": bool(row[20]),
                "review_outcome": row[21],
                "transcript": ({
                    "id": int(row[13]), "transcript_hash": row[14], "text": row[15],
                    "segments": row[16] or [],
                } if row[13] is not None else None),
                "scale": ({
                    "id": int(row[17]), "direction_id": int(row[18]),
                    "content_hash": row[19], "criteria": scale_criteria,
                } if row[17] is not None else None),
            }
    except Exception as exc:
        if is_schema_compat_error(exc):
            raise RuntimeSchemaUnavailable(
                "immutable adjudication source schema is unavailable") from exc
        raise
    finally:
        conn.close()


def get_cached_evaluation(*, call_id: int, evaluation_fingerprint: str,
                          run_kinds=("standard", "force", "batch")) -> dict | None:
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute(
                # degraded-прогон (retrieval кратко упал, но LLM отработал) НЕ исключаем
                # из кэша: иначе каждое открытие такой карточки заново вызывало бы платный
                # LLM. Просто предпочитаем не-degraded прогон, а degraded отдаём как fallback,
                # если чистого прогона с тем же fingerprint ещё нет.
                """SELECT e.id::text,e.payload,e.knowledge_revision,e.knowledge_snapshot_id,
                          e.scale_revision_id,e.created_at,e.run_kind,e.transcript_cache_id,
                          e.pair_id::text,e.evaluation_fingerprint::text,s.criteria_manifest,
                          NOT EXISTS (
                              SELECT 1 FROM ai_evaluation_runs newer
                               WHERE newer.call_id=e.call_id
                                 AND newer.status='succeeded'
                                 AND newer.run_kind = ANY(%s)
                                 AND (newer.created_at,newer.id::text) >
                                     (e.created_at,e.id::text)
                          ) AS is_latest
                     FROM ai_evaluation_runs e
                     LEFT JOIN qa_scale_revisions s ON s.id=e.scale_revision_id
                    WHERE e.call_id=%s AND e.evaluation_fingerprint=%s AND e.status='succeeded'
                      AND e.run_kind = ANY(%s)
                    ORDER BY (
                          coalesce(e.retrieval_config->>'enabled','false') = 'true'
                          AND coalesce(e.payload->'_retrieval_trace'->>'status',
                                       e.payload->'retrieval_trace'->>'status','') = 'degraded'
                      ) ASC, e.created_at DESC
                    LIMIT 1""",
                (["standard", "force", "batch"], int(call_id),
                 evaluation_fingerprint, list(run_kinds)),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"id": row[0], "payload": row[1] or {}, "knowledge_revision": row[2],
                    "knowledge_snapshot_id": row[3], "scale_revision_id": row[4],
                    "created_at": row[5], "run_kind": row[6], "transcript_cache_id": row[7],
                    "pair_id": row[8], "evaluation_fingerprint": row[9],
                    "scale_manifest": row[10] or [], "is_latest": bool(row[11])}
    except Exception as exc:
        if is_schema_compat_error(exc):
            raise RuntimeSchemaUnavailable("ai_evaluation_runs schema is unavailable") from exc
        raise
    finally:
        conn.close()


def _usage_totals(llm_meta: dict | None) -> dict:
    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_read_tokens": 0, "cache_write_tokens": 0}
    for call in (llm_meta or {}).get("calls") or []:
        usage = call.get("usage") or {}
        totals["input_tokens"] += int(usage.get("input_tokens") or 0)
        totals["output_tokens"] += int(usage.get("output_tokens") or 0)
        totals["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
        totals["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
    return totals


def _estimate_cost(usage: dict) -> float | None:
    """Use deployment-supplied prices; never bake a time-sensitive price table."""
    keys = {
        "input_tokens": "CLAUDE_INPUT_USD_PER_MTOK",
        "output_tokens": "CLAUDE_OUTPUT_USD_PER_MTOK",
        "cache_read_tokens": "CLAUDE_CACHE_READ_USD_PER_MTOK",
        "cache_write_tokens": "CLAUDE_CACHE_WRITE_USD_PER_MTOK",
    }
    prices = {}
    for usage_key, env_key in keys.items():
        raw = config.env(env_key)
        if raw is None:
            return None
        prices[usage_key] = float(raw)
    return round(sum(usage[key] * prices[key] for key in keys) / 1_000_000, 8)


def _uuid_or_none(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def save_evaluation_run(*, run_id: str, call_id: int, direction_id: int,
                        transcript_cache_id: int | None, transcript_hash: str,
                        evaluation_fingerprint: str, fingerprint_components: dict,
                        run_kind: str, model: str, model_config_hash: str,
                        prompt_hash: str, output_schema_hash: str,
                        output_schema_version: str, criteria_hash: str,
                        criterion_config_hash: str, scale_revision_id: int | None,
                        knowledge_snapshot_id: int | None, knowledge_revision: int | None,
                        retrieval_config: dict, status: str, per_criterion: list,
                        payload: dict, started_at: datetime, completed_at: datetime,
                        pair_id=None, primary_run_id=None, llm_meta: dict | None = None,
                        error_code=None, error_message=None,
                        estimated_cost=_AUTO_COST) -> str:
    """Atomically persist one final run and its normalized retrieval facts."""
    usage = _usage_totals(llm_meta)
    if estimated_cost is _AUTO_COST:
        estimated_cost = _estimate_cost(usage)
    latency_ms = max(0, round((completed_at - started_at).total_seconds() * 1000))
    trace = (payload or {}).get("_retrieval_trace") or (payload or {}).get("retrieval_trace") or {}
    retrieval_hash = content_hash(retrieval_config or {})
    conn = config.connect_rw()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ai_evaluation_runs
                           (id,call_id,direction_id,transcript_cache_id,transcript_hash,
                            evaluation_fingerprint,fingerprint_version,fingerprint_components,
                            run_kind,pair_id,primary_run_id,llm_provider,model,model_config_hash,prompt_hash,
                            output_schema_hash,output_schema_version,criteria_hash,
                            criterion_config_hash,scale_revision_id,knowledge_snapshot_id,
                            knowledge_revision,retrieval_config,retrieval_config_hash,status,
                            per_criterion,payload,error_code,error_message,latency_ms,input_tokens,
                            output_tokens,cache_read_tokens,cache_write_tokens,estimated_cost,
                            started_at,completed_at)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'anthropic',%s,%s,%s,%s,%s,%s,
                                 %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (str(run_id), int(call_id), int(direction_id), transcript_cache_id,
                     transcript_hash, evaluation_fingerprint,
                     int(fingerprint_components.get("fingerprint_version") or 1),
                     Json(fingerprint_components), run_kind, pair_id, primary_run_id,
                     model, model_config_hash,
                     prompt_hash, output_schema_hash, output_schema_version, criteria_hash,
                     criterion_config_hash, scale_revision_id, knowledge_snapshot_id,
                     knowledge_revision, Json(retrieval_config or {}), retrieval_hash, status,
                     Json(per_criterion or []), Json(payload or {}), error_code, error_message,
                     latency_ms, usage["input_tokens"], usage["output_tokens"],
                     usage["cache_read_tokens"], usage["cache_write_tokens"], estimated_cost,
                     started_at, completed_at),
                )
                _insert_retrieval_trace(
                    cur, trace=trace, evaluation_run_id=str(run_id), call_id=int(call_id),
                    direction_id=int(direction_id), knowledge_snapshot_id=knowledge_snapshot_id,
                    transcript_hash=transcript_hash, fallback_config=retrieval_config or {},
                    completed_at=completed_at, evaluation_succeeded=status == "succeeded",
                )
        return str(run_id)
    except Exception as exc:
        if is_schema_compat_error(exc):
            raise RuntimeSchemaUnavailable("immutable evaluation/trace schema is unavailable") from exc
        raise
    finally:
        conn.close()


def _insert_retrieval_trace(cur, *, trace: dict, evaluation_run_id: str, call_id: int,
                            direction_id: int, knowledge_snapshot_id,
                            transcript_hash: str, fallback_config: dict,
                            completed_at: datetime, evaluation_succeeded: bool):
    cfg = trace.get("config") or fallback_config or {}
    candidates = trace.get("candidates") or []
    latency_ms = max(0, int(trace.get("latency_ms") or 0))
    started_at = completed_at - timedelta(milliseconds=latency_ms)
    status_map = {"ok": "succeeded", "no_match": "succeeded",
                  "disabled": "skipped", "skipped": "skipped", "degraded": "failed"}
    trace_status = trace.get("status")
    status = status_map.get(trace_status, "failed" if evaluation_succeeded else "skipped")
    errors = trace.get("errors") or []
    retrieval_id = str(uuid.uuid4())
    query_manifest = trace.get("query") or {}
    # Never persist raw transcript/query text here; the transcript cache is the
    # authoritative PII artifact and the trace only needs a content identity.
    query_hash = content_hash({"transcript_hash": transcript_hash,
                               "chunks": query_manifest.get("chunks") or []})
    cur.execute(
        """INSERT INTO qa_retrieval_runs
               (id,evaluation_run_id,call_id,direction_id,criterion_id,
                knowledge_snapshot_id,retrieval_config,retrieval_config_hash,
                query_hash,query_manifest,status,error_code,error_message,latency_ms,
                candidate_count,included_count,started_at,completed_at)
             VALUES (%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (retrieval_id, evaluation_run_id, call_id, direction_id, knowledge_snapshot_id,
         Json(cfg), content_hash(cfg), query_hash, Json(query_manifest), status,
         "retrieval_degraded" if trace_status == "degraded" else None,
         "; ".join(str(item) for item in errors)[:2000] if errors else None,
         latency_ms, len(candidates), sum(1 for item in candidates if item.get("included")),
         started_at, completed_at),
    )
    for item in candidates:
        criterion_id = str(item.get("criterion_id") or "")
        rule_ref = str(item.get("rule_id") or item.get("id") or "")
        if not criterion_id or not rule_ref:
            continue
        included = bool(item.get("included"))
        reject_reason = item.get("reject_reason") or (None if included else "not_selected")
        cur.execute(
            """INSERT INTO qa_retrieval_hits
                   (retrieval_run_id,criterion_id,rule_ref,source_type,rule_id,
                    rule_version_id,rank,dense_rank,lexical_rank,similarity,dense_score,
                    lexical_score,fused_score,included,candidate_status,reject_reason,metadata)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (retrieval_id, criterion_id, rule_ref, item.get("source_type") or "canonical",
             _uuid_or_none(item.get("rule_id")), _int_or_none(item.get("rule_version_id")),
             max(1, int(item.get("rank") or 1)), _int_or_none(item.get("dense_rank")),
             _int_or_none(item.get("lexical_rank")), item.get("similarity"),
             item.get("similarity"), item.get("lexical_score"), item.get("fused_score"),
             included, "selected" if included else "rejected", reject_reason,
             Json({"best_chunk_idx": item.get("best_chunk_idx"),
                   "content_hash": item.get("content_hash")})),
        )
    if evaluation_succeeded:
        canonical_ids = sorted({_uuid_or_none(item.get("rule_id")) for item in candidates
                                if item.get("included") and _uuid_or_none(item.get("rule_id"))})
        if canonical_ids:
            cur.execute(
                """UPDATE qa_policy_rule_metrics
                      SET successful_evaluation_count=successful_evaluation_count+1,
                          updated_at=now()
                    WHERE rule_id = ANY(%s::uuid[])""",
                (canonical_ids,),
            )


def new_run_id() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
