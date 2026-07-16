"""Слой данных для раздела «ИИ-оценка» (форма контракта фронтенда src/components/call_qa).
Тяжёлые операции (review_payload) запускают реальный пайплайн: GCS → Soniox → Claude."""
from __future__ import annotations
import os
import logging
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from psycopg2.extras import Json

from . import config
from .asr import soniox
from .evaluation import criteria as criteria_mod
from .evaluation import criterion_config as cc
from .evaluation import evaluator
from .evaluation import runtime_store
from .evaluation.fingerprint import (build_evaluation_fingerprint, content_hash,
                                     transcript_fingerprint)
from .review import queue as review_queue
from .review.evidence import (EvidenceValidationError, VALID_VERDICTS,
                              locate_excerpt, validate_evidence)


def review_queue_list(limit: int = 30) -> list[dict]:
    """Очередь ревью: ИИ-оценённые звонки (текущий тег модели), которые человек ещё не
    проверял. Причины считаются из сохранённой карточки; сортировка — сначала критичное,
    внутри — свежее. stale=True — сохранённая оценка не совпадает с актуальной
    конфигурацией (промпт/шкала/база знаний/RAG-режим изменились или immutable-прогона
    нет), поэтому открытие карточки автоматически переоценит звонок. Если миграция меты
    ещё не прошла — fallback на «последние звонки»."""
    conn = None
    try:
        conn = config.connect_ro()
        cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT rc.call_id, d.name, u.name, TO_CHAR(c.created_at,'DD.MM HH24:MI'), c.score,
                      rc.payload->'criteria', rc.payload->'asr_mean_conf', rc.created_at,
                      c.direction_id, run.evaluation_fingerprint::text, run.fingerprint_components
                 FROM ai_review_cache rc
                 JOIN calls c ON c.id = rc.call_id
                 LEFT JOIN directions d ON c.direction_id = d.id
                 LEFT JOIN users u ON c.operator_id = u.id
                 LEFT JOIN ai_evaluation_meta m ON m.call_id = rc.call_id AND m.model = rc.model
                 LEFT JOIN LATERAL (
                     SELECT r.evaluation_fingerprint, r.fingerprint_components
                       FROM ai_evaluation_runs r
                      WHERE r.call_id = rc.call_id AND r.status = 'succeeded'
                        AND r.run_kind IN ('standard','force','batch')
                      ORDER BY r.created_at DESC, r.id::text DESC
                      LIMIT 1
                 ) run ON true
                WHERE rc.model = %s AND m.review_outcome IS NULL
                ORDER BY rc.created_at DESC LIMIT %s""",
            (config.CLAUDE_MODEL, max(limit * 3, limit)),  # запас: часть окажется «чистой»
        )
        rows = cur.fetchall(); cur.close(); conn.close()
        prio = review_queue.REASON_PRIORITY
        items = []
        for r in rows:
            reasons = review_queue.review_reasons(r[5] or [], r[6])
            items.append({"id": r[0], "direction": r[1], "operator": r[2] or "—",
                          "datetime": r[3], "human_score": r[4], "reasons": reasons or ["ok"],
                          "_sev": min((prio.index(x) for x in reasons), default=len(prio)),
                          "_ts": r[7], "_direction_id": r[8],
                          "_run_fp": r[9], "_run_components": r[10]})
        items.sort(key=lambda i: (i["_sev"], -(i["_ts"].timestamp() if i["_ts"] else 0)))
        items = items[:limit]
        _flag_stale_evaluations(items)
        for i in items:
            for key in ("_sev", "_ts", "_direction_id", "_run_fp", "_run_components"):
                i.pop(key, None)
        return items
    except Exception as exc:
        if runtime_store.is_schema_compat_error(exc):
            logging.warning("ai-qa: очередь работает в режиме совместимости без evaluation meta")
            return _recent_calls_fallback(limit)
        logging.exception("ai-qa: очередь ревью недоступна")
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _recent_calls_fallback(limit: int) -> list[dict]:
    """Старое поведение очереди (до появления следа ревью): последние звонки ОП с записью."""
    conn = config.connect_ro()
    cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
    cur.execute(
        """SELECT c.id, d.name, u.name, TO_CHAR(c.created_at,'DD.MM HH24:MI'), c.score
             FROM calls c
             LEFT JOIN directions d ON c.direction_id = d.id
             LEFT JOIN users u ON c.operator_id = u.id
            WHERE c.direction_id = ANY(%s) AND c.audio_path IS NOT NULL AND c.audio_path <> ''
              AND COALESCE(c.is_draft, FALSE) = FALSE
            ORDER BY c.created_at DESC LIMIT %s""",
        (config.OP_DIRECTION_IDS, limit),
    )
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id": r[0], "direction": r[1], "operator": r[2] or "—",
             "datetime": r[3], "human_score": r[4], "reasons": ["new"]} for r in rows]


def _direction_identity_context(direction_id: int) -> dict | None:
    """Направленческая часть evaluation identity, строго read-only (для очереди ревью).
    None — не удалось вычислить; очередь при этом не падает (stale останется None)."""
    from .rag import knowledge
    direction = criteria_mod.load_direction(int(direction_id))
    cc.apply_to_direction(direction)
    rollout = _rag_rollout(int(direction_id), 0)  # bucket пер-звонковый, здесь не используется
    snapshot_hash = None
    conn = config.connect_ro()
    try:
        snapshot_hash = knowledge.peek_knowledge_snapshot_hash(conn, direction=direction)
    finally:
        conn.close()
    return {"direction": direction, "mode": rollout["mode"],
            "canary_percent": rollout["canary_percent"], "snapshot_hash": snapshot_hash}


def _flag_stale_evaluations(items: list[dict]) -> None:
    """Помечает элементы очереди, чью сохранённую оценку открытие пересчитает заново.

    Открытие карточки (review_payload) отдаёт кэш только при точном совпадении
    evaluation_fingerprint с актуальной конфигурацией. Здесь ожидаемый fingerprint
    восстанавливается без записи в БД: транскрипт-компонент берётся из последнего
    прогона (аудио и ASR-конфиг звонка неизменны в норме), остальные — из текущего
    состояния направления. stale: True — открытие переоценит; False — кэш совпадёт;
    None — определить не удалось."""
    contexts: dict[int, dict | None] = {}
    for item in items:
        # Нет успешного immutable-прогона (карточка из старого кэша) —
        # открытие гарантированно запустит новую оценку.
        item["stale"] = True
        components = item.get("_run_components") or {}
        transcript_identity = components.get("transcript_hash")
        direction_id, run_fp = item.get("_direction_id"), item.get("_run_fp")
        if not (run_fp and transcript_identity and direction_id):
            continue
        try:
            if direction_id not in contexts:
                try:
                    contexts[direction_id] = _direction_identity_context(direction_id)
                except Exception:
                    logging.exception(
                        "ai-qa: очередь — не удалось построить identity направления %s", direction_id)
                    contexts[direction_id] = None
            ctx = contexts[direction_id]
            if ctx is None:
                item["stale"] = None
                continue
            bucket = _canary_bucket(direction_id, item["id"])
            use_rag = ctx["mode"] == "active" or (
                ctx["mode"] == "canary"
                and bucket < max(0, min(100, ctx["canary_percent"])))
            if use_rag and not ctx["snapshot_hash"]:
                continue  # снапшота под текущую шкалу ещё нет — открытие создаст новый
            expected, _, _ = _evaluation_identity(
                transcript_hash=transcript_identity, direction=ctx["direction"],
                knowledge_snapshot={"content_hash": ctx["snapshot_hash"]}, use_rag=use_rag)
            item["stale"] = expected != run_fp
        except Exception:
            logging.exception(
                "ai-qa: не удалось определить актуальность оценки звонка %s", item.get("id"))
            item["stale"] = None


def _download(audio_path: str, dest: str):
    from google.oauth2 import service_account
    from google.cloud import storage
    sa = config.google_sa_info()
    creds = service_account.Credentials.from_service_account_info(sa)
    bucket, blob = audio_path.split("/", 1)
    storage.Client(project=sa["project_id"], credentials=creds).bucket(bucket).blob(blob).download_to_filename(dest)


def _signed_url(audio_path, minutes=30):
    """Подписанная ссылка на запись в GCS (для прослушивания в браузере)."""
    if not audio_path:
        return None
    try:
        from datetime import timedelta
        from google.oauth2 import service_account
        from google.cloud import storage
        sa = config.google_sa_info()
        creds = service_account.Credentials.from_service_account_info(sa)
        bucket, blob = audio_path.split("/", 1)
        b = storage.Client(project=sa["project_id"], credentials=creds).bucket(bucket).blob(blob)
        return b.generate_signed_url(version="v4", expiration=timedelta(minutes=minutes),
                                     method="GET", response_type="audio/mpeg")
    except Exception:
        return None


def _audio_object_fingerprint(call_id: int, audio_path: str) -> str:
    """Stable identity for the app's UUID-addressed, immutable audio objects.

    A remote metadata lookup before the cache lookup added latency and made the
    key change whenever GCS metadata was temporarily unavailable.  The upload
    contract already guarantees a new UUID path for new audio, so call + path is
    the exact durable identity used by ASR caching.
    """
    return runtime_store.audio_fingerprint(call_id=call_id, audio_path=audio_path)


def _slim_asr_tokens(tokens: list[dict]) -> list[dict]:
    fields = ("text", "speaker", "language", "confidence", "start_time_ms", "end_time_ms")
    return [{key: token.get(key) for key in fields if token.get(key) is not None}
            for token in tokens]


def current_rag_experiment_config() -> dict:
    """Canonical runtime contract an approvable paired experiment must freeze."""
    return {
        "version": 1,
        "model": config.CLAUDE_MODEL,
        "model_config": {
            "bulk": config.CLAUDE_MODEL_BULK,
            "hard": config.CLAUDE_MODEL_HARD,
            "effort": config.CLAUDE_EFFORT,
            "escalate_conf": config.ESCALATE_CONF,
        },
        "prompt_template_hash": content_hash(evaluator.build_system([])),
        "output_schema_hash": content_hash(evaluator._OUTPUT_SCHEMA),
        "retrieval_config": _retrieval_config(enabled=True),
    }


def _approved_experiment_state(cur, *, direction_id: int, experiment_id) -> dict:
    """Validate that a rollout approval still describes this direction's live snapshot."""
    if not experiment_id:
        return {"valid": False, "reason": "контрольная проверка не выбрана"}
    try:
        experiment_id = str(uuid.UUID(str(experiment_id)))
    except (ValueError, TypeError, AttributeError):
        return {"valid": False, "reason": "некорректный ID контрольной проверки"}
    cur.execute(
        """SELECT x.metrics,x.status,x.knowledge_snapshot_id,x.evaluation_config,
                  x.evaluation_config_hash,x.model,
                  (SELECT s.current_snapshot_id
                     FROM qa_knowledge_state s
                     JOIN qa_scale_revisions sr ON sr.id=s.scale_revision_id
                    WHERE s.direction_id=%s
                    ORDER BY sr.scale_revision DESC LIMIT 1),
                  (SELECT COUNT(*) FROM qa_gold_labels gl WHERE gl.gold_set_id=x.gold_set_id),
                  (SELECT COUNT(*) FROM qa_gold_labels gl
                    WHERE gl.gold_set_id=x.gold_set_id AND gl.direction_id<>%s)
             FROM qa_rag_experiments x WHERE x.id=%s""",
        (int(direction_id), int(direction_id), experiment_id),
    )
    row = cur.fetchone()
    if not row:
        return {"valid": False, "reason": "контрольная проверка не найдена"}
    (metrics, status, approved_snapshot, experiment_config, config_hash, experiment_model,
     current_snapshot, labels, foreign_labels) = row
    from .evaluation.benchmark import evaluate_quality_gates
    stored_quality = (metrics or {}).get("quality_gates") or {}
    report = (metrics or {}).get("report") or (metrics or {})
    recomputed_quality = evaluate_quality_gates(report)
    if (status != "succeeded" or not bool(stored_quality.get("passed"))
            or not recomputed_quality["passed"]):
        return {"valid": False, "reason": "контрольная проверка не прошла quality gates"}
    if not labels or foreign_labels:
        return {"valid": False, "reason": "контрольная выборка не привязана только к этому направлению"}
    expected_config = current_rag_experiment_config()
    expected_config_hash = content_hash(expected_config)
    if (str(experiment_model) != str(config.CLAUDE_MODEL)
            or content_hash(experiment_config or {}) != str(config_hash)
            or str(config_hash) != expected_config_hash):
        return {"valid": False, "reason": "конфигурация оценки изменилась после контрольной проверки",
                "evaluation_config_hash": str(config_hash),
                "current_evaluation_config_hash": expected_config_hash}
    if approved_snapshot is None or current_snapshot is None or int(approved_snapshot) != int(current_snapshot):
        return {"valid": False, "reason": "база знаний изменилась после контрольной проверки",
                "approved_snapshot_id": approved_snapshot, "current_snapshot_id": current_snapshot}
    return {"valid": True, "reason": None, "approved_snapshot_id": int(approved_snapshot),
            "current_snapshot_id": int(current_snapshot), "evaluation_config_hash": str(config_hash)}


def _bind_rollout_approval(gates: dict, approval: dict) -> dict:
    """Invalidate an experiment changed after it was explicitly approved for rollout."""
    if (approval.get("valid") and
            str((gates or {}).get("approved_evaluation_config_hash") or "") !=
            str(approval.get("evaluation_config_hash") or "")):
        return {**approval, "valid": False,
                "reason": "одобренная конфигурация experiment больше не совпадает"}
    return approval


def _canary_bucket(direction_id: int, call_id: int) -> int:
    """Детерминированная канарейка звонка: одна формула для оценки и очереди ревью."""
    return int(content_hash({"call_id": int(call_id), "direction_id": int(direction_id)})[:8], 16) % 100


def _rag_rollout(direction_id: int, call_id: int) -> dict:
    """Resolve DB-controlled rollout with a deterministic canary assignment."""
    mode, percent, source = config.RAG_MODE, config.RAG_CANARY_PERCENT, "environment"
    try:
        conn = config.connect_ro()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT rollout_mode,canary_percent,quality_gates,approved_experiment_id::text
                         FROM qa_rag_rollout_config WHERE direction_id=%s""",
                    (int(direction_id),),
                )
                row = cur.fetchone()
                if row:
                    mode, percent, source = str(row[0]), int(row[1]), "database"
                    gates, approved = row[2] or {}, row[3]
                    approval = _approved_experiment_state(
                        cur, direction_id=direction_id, experiment_id=approved)
                    approval = _bind_rollout_approval(gates, approval)
                else:
                    gates, approved = {}, None
                    approval = {"valid": False, "reason": "настройка rollout отсутствует"}
        finally:
            conn.close()
    except Exception as exc:
        logging.info("ai-qa: rollout config fallback for direction %s: %s", direction_id, exc)
        gates, approved = {}, None
        approval = {"valid": False, "reason": "не удалось проверить контрольную проверку"}
    if mode not in {"off", "shadow", "canary", "active"}:
        mode = "shadow"
    if mode in {"canary", "active"} and not approval["valid"]:
        manual = (gates or {}).get("manual_override")
        if manual:
            # Явное ручное включение суперадмином: RAG влияет на показываемую оценку
            # без paired experiment. Рантайм-предохранители сохраняются (в retrieval
            # участвуют только active+indexed правила с совпавшим контрактом эмбеддингов
            # и similarity ≥ порога; retrieval fail-closed; полный audit-trace).
            # Обратимо мгновенной сменой режима на shadow/off.
            approval = {"valid": True, "reason": None, "manual": True,
                        "override_by": manual.get("by"), "override_reason": manual.get("reason")}
        else:
            logging.warning("ai-qa: rollout for direction %s forced to shadow: %s",
                            direction_id, approval["reason"])
            mode, percent, source = "shadow", 0, "database-safe-fallback"
    bucket = _canary_bucket(direction_id, call_id)
    selected = mode == "active" or (mode == "canary" and bucket < max(0, min(100, percent)))
    return {"mode": mode, "canary_percent": percent, "canary_bucket": bucket,
            "rag_enabled": selected, "shadow_enabled": mode == "shadow",
            "source": source, "quality_gates": gates, "approved_experiment_id": approved,
            "approval": approval}


def _retrieval_config(*, enabled: bool) -> dict:
    from .embeddings.provider import configured_contract
    embedding_contract = configured_contract()
    return {
        "enabled": bool(enabled), "pipeline": "hybrid-set-v2",
        "embedding_provider": embedding_contract["provider"],
        "embedding_model": embedding_contract["model"],
        "embedding_dim": embedding_contract["dim"],
        "embedding_config_hash": embedding_contract["config_hash"],
        "embedding_config": embedding_contract["config"],
        "top_k": config.RETRIEVAL_TOP_K,
        "min_similarity": config.RETRIEVAL_MIN_SIMILARITY,
        "lexical_min_score": config.RETRIEVAL_LEXICAL_MIN_SCORE,
        "lexical_dense_margin": config.RETRIEVAL_LEXICAL_DENSE_MARGIN,
        "candidate_multiplier": config.RETRIEVAL_CANDIDATE_MULTIPLIER,
        "chunk_chars": config.EMBED_CHUNK_CHARS,
        "chunk_overlap": config.EMBED_CHUNK_OVERLAP,
        "max_chunks": config.EMBED_MAX_CHUNKS,
    }


def _evaluation_identity(*, transcript_hash: str, direction: dict,
                         knowledge_snapshot: dict, use_rag: bool) -> tuple[str, dict, dict]:
    transcript_criteria = [c for c in direction["criteria"] if c.get("eval_source") == cc.TRANSCRIPT]
    model_config = {
        "bulk": config.CLAUDE_MODEL_BULK, "hard": config.CLAUDE_MODEL_HARD,
        "effort": config.CLAUDE_EFFORT, "escalate_conf": config.ESCALATE_CONF,
    }
    criterion_cfg = [{"criterion_id": c["criterion_id"], "eval_source": c.get("eval_source"),
                      "default_verdict": c.get("default_verdict")}
                     for c in direction["criteria"]]
    retrieval_cfg = _retrieval_config(enabled=use_rag)
    fingerprint, components = build_evaluation_fingerprint(
        transcript_hash=transcript_hash, model=config.CLAUDE_MODEL,
        model_config=model_config,
        prompt_hash=content_hash(evaluator.build_system(transcript_criteria)),
        output_schema_hash=content_hash(evaluator._OUTPUT_SCHEMA),
        scale_hash=direction["scale_hash"],
        criterion_config_hash=content_hash(criterion_cfg),
        knowledge_snapshot_hash=(knowledge_snapshot["content_hash"] if use_rag else "rag-disabled"),
        retrieval_config=retrieval_cfg,
        evaluator_code_version=config.EVALUATOR_CODE_VERSION,
    )
    components["criterion_config"] = criterion_cfg
    return fingerprint, components, retrieval_cfg


def _evaluation_summary(*, run_id: str, fingerprint: str, knowledge_snapshot: dict,
                        trace: dict, rollout: dict, stale=False) -> dict:
    criteria_trace = trace.get("criteria") or []
    return {
        "run_id": run_id, "fingerprint_short": fingerprint[:12],
        "knowledge_revision": knowledge_snapshot.get("knowledge_revision"),
        "retrieval_status": trace.get("status") or "unknown",
        "retrieved_count": sum(int(item.get("retrieved_count") or 0) for item in criteria_trace),
        "included_count": sum(int(item.get("included_count") or 0) for item in criteria_trace),
        "retrieval_ms": int(trace.get("latency_ms") or 0), "stale": bool(stale),
        "rollout_mode": rollout.get("mode"), "rag_enabled": rollout.get("rag_enabled"),
    }


def _hydrate_cached_card_binding(card: dict, cached_run: dict) -> bool:
    """Restore/verify binding metadata on rolling-deploy immutable payloads."""
    manifest = cached_run.get("scale_manifest") or []
    criteria = card.get("criteria") or []
    if (not cached_run.get("id") or cached_run.get("scale_revision_id") is None or
            not cached_run.get("evaluation_fingerprint") or not manifest or not criteria):
        return False
    scale_by_idx = {}
    for row in manifest:
        if not isinstance(row, dict):
            return False
        try:
            idx = int(row["criterion_idx"])
        except (KeyError, TypeError, ValueError):
            return False
        if idx in scale_by_idx or not row.get("criterion_id"):
            return False
        scale_by_idx[idx] = row
    seen = set()
    for criterion in criteria:
        if not isinstance(criterion, dict):
            return False
        try:
            idx = int(criterion.get("idx"))
        except (TypeError, ValueError):
            return False
        scale = scale_by_idx.get(idx)
        criterion_id = str((scale or {}).get("criterion_id") or "")
        if (not scale or idx in seen or
                (criterion.get("criterion_id") and
                 str(criterion["criterion_id"]) != criterion_id) or
                str(criterion.get("name") or "") != str(scale.get("criterion_name") or "") or
                criterion.get("source") != scale.get("eval_source")):
            return False
        seen.add(idx)
        criterion["criterion_id"] = criterion_id
    if seen != set(scale_by_idx):
        return False
    card["_evaluation_run_id"] = str(cached_run["id"])
    card["_scale_revision_id"] = int(cached_run["scale_revision_id"])
    card["_evaluation_fingerprint"] = str(cached_run["evaluation_fingerprint"])
    return True


def _norm_verdict(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in {"correct", "ok", "да", "верно", "true", "1"}:
        return "Correct"
    if s in {"incorrect", "error", "нет", "неверно", "false", "0"}:
        return "Incorrect"
    if s in {"n/a", "na", "неприменимо", "-", ""}:
        return "N/A"
    return str(v)


def _ai_score(direction: dict, result: dict):
    """Балл ИИ по той же формуле, что и человеческий (main.jsx): критический Incorrect → 0;
    иначе сумма весов НЕкритических критериев со статусом Correct/N/A. Критерии, которые ИИ
    не может проверить (system_api/manual → Pending), считаем зачётом (benefit of the doubt).
    Но Pending по TRANSCRIPT-критерию = модель не вернула вердикт даже после повтора —
    оценка неполная, балла нет (None): сбой не должен превращаться в незаслуженный зачёт."""
    rows = result.get("per_criterion", [])
    if any(r.get("source") == "transcript" and r.get("verdict") == "Pending" for r in rows):
        return None
    verdict = {r["idx"]: r["verdict"] for r in rows}
    crits = direction.get("criteria", [])
    for c in crits:
        if c.get("is_critical") and verdict.get(c["idx"]) == "Incorrect":
            return 0
    total = 0.0
    for c in crits:
        if c.get("is_critical"):
            continue
        if verdict.get(c["idx"]) in ("Correct", "N/A", "Pending"):
            total += (c.get("weight") or 0)
    return round(total)


def _lines_from_tokens(toks: list[dict]) -> list[dict]:
    """Soniox tokens -> review lines with confidence and source time ranges.

    Time ranges are retained so a human-selected excerpt can be tied back to the
    audio instead of existing only as model-generated prose.
    """
    cnt = {}
    for t in toks:
        sp = t.get("speaker")
        if sp is not None:
            cnt[sp] = cnt.get(sp, 0) + 1
    op = max(cnt, key=cnt.get) if cnt else None  # оператор = кто больше говорит
    lines, seg = [], []
    cur_sp = object()
    line_start = line_end = None

    def flush():
        if seg:
            line = {"speaker": "operator" if cur_sp == op else "client", "seg": list(seg)}
            if line_start is not None:
                line["start_ms"] = line_start
            if line_end is not None:
                line["end_ms"] = line_end
            lines.append(line)

    for t in toks:
        sp = t.get("speaker")
        if sp != cur_sp:
            flush(); seg.clear(); cur_sp = sp
            line_start = t.get("start_time_ms")
            line_end = t.get("end_time_ms")
        else:
            if line_start is None:
                line_start = t.get("start_time_ms")
            if t.get("end_time_ms") is not None:
                line_end = t.get("end_time_ms")
        txt = t.get("text", ""); c = t.get("confidence")
        timing = {k: t.get(k) for k in ("start_time_ms", "end_time_ms") if t.get(k) is not None}
        if c is not None and c < config.ASR_CONF_HARD:
            seg.append({"t": txt, "c": round(c, 2), **timing})
        elif seg and "c" not in seg[-1] and not timing:
            seg[-1]["t"] += txt
        else:
            seg.append({"t": txt, **timing})
    flush()
    return lines


def _cache_get(call_id, model):
    try:
        conn = config.connect_ro(); cur = conn.cursor()
        cur.execute("SELECT payload FROM ai_review_cache WHERE call_id=%s AND model=%s", (call_id, model))
        row = cur.fetchone(); cur.close(); conn.close()
        return row[0] if row else None
    except Exception:
        return None  # таблицы ещё нет


def _cache_put(call_id, model, payload, strict=False):
    try:
        from psycopg2.extras import Json
        conn = config.connect_rw()
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_review_cache (call_id, model, payload, created_at)
                   VALUES (%s,%s,%s, now())
                   ON CONFLICT (call_id, model) DO UPDATE SET payload=EXCLUDED.payload, created_at=now()""",
                (call_id, model, Json(payload)))
        conn.close()
    except Exception:
        if strict:
            raise  # пакетная оценка: потеря результата недопустима — наверху ретрай
        pass  # карточка ревью: best-effort (нет RW/таблицы) — просто не кэшируем


def _meta_upsert(call_id, model, payload):
    """Журнал оценки в ai_evaluation_meta: needs_review + причины из карточки.
    Новая оценка сбрасывает след ревью (переоценили → человек проверяет заново).
    Best-effort: без RW/миграции оценка важнее журнала."""
    try:
        from psycopg2.extras import Json
        reasons = review_queue.review_reasons(payload.get("criteria"), payload.get("asr_mean_conf"))
        conn = config.connect_rw()
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_evaluation_meta
                     (call_id, direction_id, model, per_criterion, asr_mean_conf, needs_review, review_reasons)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (call_id, model) DO UPDATE SET
                     per_criterion=EXCLUDED.per_criterion, asr_mean_conf=EXCLUDED.asr_mean_conf,
                     needs_review=EXCLUDED.needs_review, review_reasons=EXCLUDED.review_reasons,
                     review_outcome=NULL, reviewed_by=NULL, reviewed_at=NULL, created_at=now()""",
                (call_id, payload.get("direction_id") or 0, model,
                 Json(payload.get("criteria") or []), payload.get("asr_mean_conf"),
                 bool(reasons), Json(reasons)))
        conn.close()
    except Exception:
        logging.exception("ai-qa: не удалось записать ai_evaluation_meta (call %s)", call_id)


def _record_review_outcome(call_id, outcome, reviewer_id=None, *, payload=None, model=None):
    """Фиксирует итог ревью («confirmed» — человек согласился, «adjudicated» — исправил):
    звонок уходит из очереди. Если меты ещё нет — создаём её из immutable карточки run."""
    try:
        from psycopg2.extras import Json
        model = model or config.CLAUDE_MODEL
        payload = payload or {}
        reasons = review_queue.review_reasons(payload.get("criteria"), payload.get("asr_mean_conf"))
        conn = config.connect_rw()
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_evaluation_meta
                     (call_id, direction_id, model, per_criterion, asr_mean_conf,
                      needs_review, review_reasons, review_outcome, reviewed_by, reviewed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                   ON CONFLICT (call_id, model) DO UPDATE SET
                     review_outcome=EXCLUDED.review_outcome,
                     reviewed_by=EXCLUDED.reviewed_by, reviewed_at=now()""",
                (call_id, payload.get("direction_id") or 0, model,
                 Json(payload.get("criteria") or []), payload.get("asr_mean_conf"),
                 bool(reasons), Json(reasons), outcome, reviewer_id))
        conn.close()
    except Exception:
        logging.exception("ai-qa: не удалось записать итог ревью (call %s)", call_id)


def _claim_review_outcome(cur, *, call_id: int, outcome: str, reviewer_id,
                          payload: dict, model: str) -> None:
    """Atomically claim one human review for the latest run under the call lock."""
    reasons = review_queue.review_reasons(
        payload.get("criteria"), payload.get("asr_mean_conf"))
    cur.execute(
        """INSERT INTO ai_evaluation_meta
                 (call_id,direction_id,model,per_criterion,asr_mean_conf,
                  needs_review,review_reasons,review_outcome,reviewed_by,reviewed_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
               ON CONFLICT (call_id,model) DO UPDATE SET
                 direction_id=EXCLUDED.direction_id,
                 per_criterion=EXCLUDED.per_criterion,
                 asr_mean_conf=EXCLUDED.asr_mean_conf,
                 needs_review=EXCLUDED.needs_review,
                 review_reasons=EXCLUDED.review_reasons,
                 review_outcome=EXCLUDED.review_outcome,
                 reviewed_by=EXCLUDED.reviewed_by,
                 reviewed_at=now()
               WHERE ai_evaluation_meta.review_outcome IS NULL
               RETURNING id""",
        (int(call_id), int(payload["direction_id"]), str(model),
         Json(payload.get("criteria") or []), payload.get("asr_mean_conf"),
         bool(reasons), Json(reasons), outcome, reviewer_id),
    )
    if not cur.fetchone():
        raise ValueError("эта evaluation_run уже проверена другим запросом")


# Параллельное открытие одной карточки (двойной клик, два админа) не должно оплачивать
# ASR+LLM дважды: тяжёлый путь сериализуется на звонок, второй запрос дожидается кэша.
# Защита в пределах процесса; при нескольких воркерах дубль остаётся возможен, но редок.
_inflight_guard = threading.Lock()
_inflight: dict[int, threading.Lock] = {}
_shadow_executor = ThreadPoolExecutor(
    max_workers=max(1, int(config.env("RAG_SHADOW_WORKERS", "2"))),
    thread_name_prefix="rag-shadow")
_shadow_guard = threading.Lock()
_shadow_inflight: set[tuple[int, str]] = set()
_reindex_executor = ThreadPoolExecutor(
    max_workers=max(1, int(config.env("RAG_REINDEX_WORKERS", "2"))),
    thread_name_prefix="rag-reindex")
_reindex_guard = threading.Lock()
_reindex_inflight: set[str] = set()


def _call_lock(call_id: int) -> threading.Lock:
    with _inflight_guard:
        return _inflight.setdefault(call_id, threading.Lock())


def _schedule_shadow_variant(**kwargs):
    key = (int(kwargs["call_id"]), str(kwargs["snapshot"]["content_hash"]))
    with _shadow_guard:
        if key in _shadow_inflight:
            return
        _shadow_inflight.add(key)

    def run():
        try:
            # Cross-worker serialization and the cache re-check inside the job
            # prevent duplicate shadow spend when several users open one card.
            with runtime_store.distributed_call_lock(kwargs["call_id"]) as lock_acquired:
                if not lock_acquired:
                    logging.error("ai-qa: shadow run skipped because advisory lock is unavailable")
                    return
                _run_shadow_variant(**kwargs)
        finally:
            with _shadow_guard:
                _shadow_inflight.discard(key)
    _shadow_executor.submit(run)


def review_payload(call_id: int, refresh: bool = False) -> dict:
    """Return a reproducible evaluation, independently caching ASR and LLM/RAG.

    The immutable cache key includes prompt, scale, criterion configuration,
    retrieval settings and the knowledge snapshot.  ``refresh`` creates another
    immutable run with the same fingerprint; it never overwrites audit history.
    """
    call_id = int(call_id)
    with _call_lock(call_id):
        with runtime_store.distributed_call_lock(call_id) as lock_acquired:
            if not lock_acquired:
                raise RuntimeError("не удалось заблокировать звонок для безопасной оценки")
            return _evaluate_and_cache(call_id, config.CLAUDE_MODEL, refresh)


def _evaluate_and_cache(call_id: int, model: str, refresh: bool) -> dict:
    conn = config.connect_ro()
    cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
    cur.execute(
        """SELECT c.id, c.direction_id, d.name, u.name,
                  TO_CHAR(c.created_at,'DD.MM.YYYY, HH24:MI'), c.score, c.audio_path
             FROM calls c
             LEFT JOIN directions d ON c.direction_id = d.id
             LEFT JOIN users u ON c.operator_id = u.id
            WHERE c.id = %s""", (call_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row:
        raise ValueError("звонок не найден")
    direction_id, audio_path = row[1], row[6]
    if not audio_path:
        raise ValueError("у звонка нет записи")

    audio_fp = _audio_object_fingerprint(call_id, audio_path)
    asr_cfg = runtime_store.asr_config()
    asr_cfg_hash = content_hash(asr_cfg)
    transcript_record = None
    try:
        transcript_record = runtime_store.get_transcript(
            call_id=call_id, audio_fingerprint_value=audio_fp, asr_provider="soniox",
            asr_model=config.SONIOX_MODEL, asr_config_hash=asr_cfg_hash)
    except runtime_store.RuntimeSchemaUnavailable:
        if config.RAG_TRACE_REQUIRED:
            raise RuntimeError("схема immutable ASR/evaluation cache не применена")

    if transcript_record:
        asm = {"text": transcript_record["text"],
               "languages": transcript_record.get("languages") or {},
               "mean_conf": transcript_record.get("mean_conf"),
               "low_conf_spans": transcript_record.get("low_conf_spans") or []}
        lines = transcript_record.get("segments") or []
        transcript_cache_id = transcript_record["id"]
        transcript_hash = transcript_record["transcript_hash"]
    else:
        # Rolling-deploy migration path: reuse the old embedded ASR artifact once,
        # then write it into the dedicated immutable transcript cache.
        legacy = _cache_get(call_id, model)
        legacy_asm = (legacy or {}).get("_asm") if (legacy or {}).get("_audio_path") == audio_path else None
        if legacy_asm and legacy_asm.get("text"):
            asm = legacy_asm
            lines = (legacy or {}).get("transcript") or []
            tokens = None
        else:
            with tempfile.TemporaryDirectory() as td:
                dest = os.path.join(td, "audio.mp3")
                _download(audio_path, dest)
                raw_tokens = soniox.transcribe_file(dest)
            full = soniox.assemble(raw_tokens)
            asm = {"text": full["text"], "languages": full["languages"],
                   "mean_conf": full["mean_conf"], "low_conf_spans": full["low_conf_spans"]}
            lines = _lines_from_tokens(raw_tokens)
            tokens = _slim_asr_tokens(raw_tokens)
        transcript_hash = content_hash(asm["text"])
        duration_ms = max((int(token.get("end_time_ms") or 0) for token in (tokens or [])), default=None)
        try:
            transcript_cache_id = runtime_store.put_transcript(
                call_id=call_id, audio_fingerprint_value=audio_fp, asr_provider="soniox",
                asr_model=config.SONIOX_MODEL, asr_config_hash=asr_cfg_hash,
                transcript_hash=transcript_hash, text=asm["text"], segments=lines,
                tokens=tokens, payload={"asr_config": asr_cfg},
                languages=asm.get("languages"), mean_conf=asm.get("mean_conf"),
                low_conf_spans=asm.get("low_conf_spans"), duration_ms=duration_ms)
        except runtime_store.RuntimeSchemaUnavailable:
            if config.RAG_TRACE_REQUIRED:
                raise RuntimeError("не удалось сохранить immutable ASR artifact")
            transcript_cache_id = None

    direction = criteria_mod.load_direction(direction_id)
    cc.apply_to_direction(direction)
    from .rag import knowledge
    knowledge_conn = config.connect_rw()
    try:
        with knowledge_conn:
            knowledge_ctx = knowledge.ensure_knowledge_context(knowledge_conn, direction=direction)
    finally:
        knowledge_conn.close()
    scale_revision_id = knowledge_ctx["scale_revision_id"]
    snapshot = knowledge_ctx["snapshot"]
    rollout = _rag_rollout(direction_id, call_id)
    primary_use_rag = bool(rollout["rag_enabled"])
    transcript_identity = transcript_fingerprint(
        audio_fingerprint=audio_fp, asr_model=config.SONIOX_MODEL,
        asr_config=asr_cfg, transcript=asm["text"])
    fingerprint, fingerprint_components, retrieval_cfg = _evaluation_identity(
        transcript_hash=transcript_identity, direction=direction,
        knowledge_snapshot=snapshot, use_rag=primary_use_rag)

    if not refresh:
        try:
            cached_run = runtime_store.get_cached_evaluation(
                call_id=call_id, evaluation_fingerprint=fingerprint)
        except runtime_store.RuntimeSchemaUnavailable:
            cached_run = None
            if config.RAG_TRACE_REQUIRED:
                raise RuntimeError("схема immutable evaluation cache не применена")
        if cached_run:
            cached = dict(cached_run["payload"] or {})
            cached["criteria"] = [dict(item) for item in (cached.get("criteria") or [])]
            if (not cached_run.get("is_latest") or
                    not _hydrate_cached_card_binding(cached, cached_run)):
                logging.info(
                    "ai-qa: immutable cache run %s is stale/incompatible; evaluating a fresh run",
                    cached_run.get("id"))
                cached_run = None
        if cached_run:
            transcript_cached = (
                runtime_store.get_transcript_by_id(cached_run["transcript_cache_id"])
                if cached_run["transcript_cache_id"] is not None else None)
            if transcript_cached:
                cached["transcript"] = transcript_cached.get("segments") or []
                cached["languages"] = transcript_cached.get("languages") or cached.get("languages") or {}
                cached["asr_mean_conf"] = transcript_cached.get("mean_conf") or 0
            cached["_transcript_cache_id"] = cached_run["transcript_cache_id"]
            cached["_transcript_hash"] = transcript_hash
            cached["_audio_path"] = audio_path
            cached["_cached"] = True
            cached["audio_url"] = _signed_url(audio_path)
            if rollout["shadow_enabled"]:
                _schedule_shadow_variant(
                    call_id=call_id, direction_id=direction_id, direction=direction, asm=asm,
                    transcript_cache_id=transcript_cache_id, transcript_hash=transcript_hash,
                    transcript_identity=transcript_identity,
                    scale_revision_id=scale_revision_id, snapshot=snapshot,
                    primary_run_id=cached_run["id"], pair_id=cached_run.get("pair_id"),
                    refresh=False)
            # Keep the legacy queue projection available, but adjudication itself
            # is bound only to the immutable run metadata hydrated above.
            if not _cache_get(call_id, model):
                _cache_put(call_id, model, cached)
            return cached

    # Пометка для ревьюера: звонок уже оценивался, но прежний результат не совпал
    # с актуальной конфигурацией (или прогона нет в immutable-кэше) — это переоценка
    # устаревшей оценки, а не первая оценка звонка.
    previous_evaluation_stale = False
    if not refresh:
        try:
            prior_fp = runtime_store.latest_evaluation_fingerprint(call_id)
        except runtime_store.RuntimeSchemaUnavailable:
            prior_fp = None
        previous_evaluation_stale = bool(prior_fp) or bool(_cache_get(call_id, model))

    crit_meta = {c["idx"]: c for c in direction["criteria"]}
    run_id = runtime_store.new_run_id()
    pair_id = runtime_store.new_run_id() if rollout["shadow_enabled"] else None
    started_at = runtime_store.now_utc()
    try:
        result = evaluator.evaluate(
            asm["text"], direction, asr_low_spans=asm["low_conf_spans"],
            use_rag=primary_use_rag, knowledge_snapshot_id=snapshot["id"])
        completed_at = runtime_store.now_utc()
    except Exception as exc:
        completed_at = runtime_store.now_utc()
        # The evaluator may fail after retrieval (for example at the LLM call).
        # Without a completed trace we must not classify that as a retrieval outage.
        failure_payload = {"id": row[0], "direction_id": direction_id}
        try:
            runtime_store.save_evaluation_run(
                run_id=run_id, call_id=call_id, direction_id=direction_id,
                transcript_cache_id=transcript_cache_id, transcript_hash=transcript_hash,
                evaluation_fingerprint=fingerprint,
                fingerprint_components=fingerprint_components,
                run_kind="force" if refresh else "standard", model=model,
                model_config_hash=content_hash(fingerprint_components["model_config"]),
                prompt_hash=fingerprint_components["prompt_hash"],
                output_schema_hash=fingerprint_components["output_schema_hash"],
                output_schema_version="ai-qa-evaluation-v2",
                criteria_hash=direction["scale_hash"],
                criterion_config_hash=fingerprint_components["criterion_config_hash"],
                scale_revision_id=scale_revision_id, knowledge_snapshot_id=snapshot["id"],
                knowledge_revision=snapshot["knowledge_revision"],
                retrieval_config=retrieval_cfg, status="failed", per_criterion=[],
                payload=failure_payload, started_at=started_at, completed_at=completed_at,
                pair_id=pair_id,
                error_code=type(exc).__name__, error_message=str(exc)[:2000])
        except Exception:
            logging.exception("ai-qa: failed to persist failed evaluation run %s", run_id)
        raise

    criteria = []
    for v in result["per_criterion"]:
        cm = crit_meta.get(v["idx"], {})
        criteria.append({
            "idx": v["idx"], "criterion_id": cm.get("criterion_id"),
            "name": v["name"], "is_critical": bool(cm.get("is_critical")),
            "source": v["source"], "ai": v["verdict"], "conf": v["confidence"],
            "evidence": v["evidence_quote"], "comment": v["comment"], "model": v.get("model"),
        })

    payload = {
        "id": row[0], "direction_id": direction_id, "direction": row[2],
        "operator": row[3] or "—", "datetime": row[4],
        "human_score": row[5], "languages": asm["languages"], "asr_mean_conf": asm["mean_conf"] or 0,
        "transcript": lines, "criteria": criteria,
        "ai_score": _ai_score(direction, result),
        "_audio_path": audio_path,
        "_transcript_cache_id": transcript_cache_id,
        "_transcript_hash": transcript_hash,
        "_evaluation_run_id": run_id,
        "_scale_revision_id": scale_revision_id,
        "_knowledge_snapshot_id": snapshot["id"],
        "_evaluation_fingerprint": fingerprint,
        "_pair_id": pair_id,
        "_retrieval_trace": result.get("retrieval_trace") or {},
        "_llm_meta": result.get("_llm_meta") or {},
        "_previous_evaluation_stale": previous_evaluation_stale,
    }
    payload["evaluation"] = _evaluation_summary(
        run_id=run_id, fingerprint=fingerprint, knowledge_snapshot=snapshot,
        trace=payload["_retrieval_trace"], rollout=rollout)

    # Keep ASR in ai_transcript_cache.  The immutable evaluation row stores only
    # references and evaluation output; the compatibility pointer remains a
    # hydrated card for the current frontend/review validator.
    persisted_payload = dict(payload)
    persisted_payload.pop("transcript", None)
    persisted_payload.pop("_audio_path", None)
    runtime_store.save_evaluation_run(
        run_id=run_id, call_id=call_id, direction_id=direction_id,
        transcript_cache_id=transcript_cache_id, transcript_hash=transcript_hash,
        evaluation_fingerprint=fingerprint, fingerprint_components=fingerprint_components,
        run_kind="force" if refresh else "standard", model=model,
        model_config_hash=content_hash(fingerprint_components["model_config"]),
        prompt_hash=fingerprint_components["prompt_hash"],
        output_schema_hash=fingerprint_components["output_schema_hash"],
        output_schema_version="ai-qa-evaluation-v2", criteria_hash=direction["scale_hash"],
        criterion_config_hash=fingerprint_components["criterion_config_hash"],
        scale_revision_id=scale_revision_id, knowledge_snapshot_id=snapshot["id"],
        knowledge_revision=snapshot["knowledge_revision"], retrieval_config=retrieval_cfg,
        status="succeeded", per_criterion=result.get("per_criterion") or [],
        payload=persisted_payload, started_at=started_at, completed_at=completed_at,
        pair_id=pair_id, llm_meta=result.get("_llm_meta"))

    if rollout["shadow_enabled"]:
        _schedule_shadow_variant(
            call_id=call_id, direction_id=direction_id, direction=direction, asm=asm,
            transcript_cache_id=transcript_cache_id, transcript_hash=transcript_hash,
            transcript_identity=transcript_identity,
            scale_revision_id=scale_revision_id, snapshot=snapshot,
            primary_run_id=run_id, pair_id=pair_id, refresh=refresh)

    _cache_put(call_id, model, payload)
    _meta_upsert(call_id, model, payload)
    payload["_cached"] = False
    payload["audio_url"] = _signed_url(audio_path)
    return payload


def _run_shadow_variant(*, call_id: int, direction_id: int, direction: dict, asm: dict,
                        transcript_cache_id, transcript_hash: str, transcript_identity: str,
                        scale_revision_id: int,
                        snapshot: dict, primary_run_id: str, pair_id: str | None,
                        refresh: bool):
    """Best-effort paired RAG-on run; it never changes the user-facing verdict."""
    fingerprint, components, retrieval_cfg = _evaluation_identity(
        transcript_hash=transcript_identity, direction=direction,
        knowledge_snapshot=snapshot, use_rag=True)
    if not refresh:
        try:
            if runtime_store.get_cached_evaluation(
                    call_id=call_id, evaluation_fingerprint=fingerprint,
                    run_kinds=("shadow",)):
                return
        except Exception:
            pass
    run_id = runtime_store.new_run_id()
    started = runtime_store.now_utc()
    try:
        result = evaluator.evaluate(
            asm["text"], direction, asr_low_spans=asm.get("low_conf_spans") or [],
            use_rag=True, knowledge_snapshot_id=snapshot["id"])
        completed = runtime_store.now_utc()
        shadow_payload = {
            "id": call_id, "direction_id": direction_id,
            "criteria": result.get("per_criterion") or [],
            "overall_comment": result.get("overall_comment"),
            "_primary_run_id": primary_run_id,
            "_pair_id": pair_id,
            "_retrieval_trace": result.get("retrieval_trace") or {},
            "_llm_meta": result.get("_llm_meta") or {},
        }
        runtime_store.save_evaluation_run(
            run_id=run_id, call_id=call_id, direction_id=direction_id,
            transcript_cache_id=transcript_cache_id, transcript_hash=transcript_hash,
            evaluation_fingerprint=fingerprint, fingerprint_components=components,
            run_kind="shadow", model=config.CLAUDE_MODEL,
            model_config_hash=content_hash(components["model_config"]),
            prompt_hash=components["prompt_hash"],
            output_schema_hash=components["output_schema_hash"],
            output_schema_version="ai-qa-evaluation-v2", criteria_hash=direction["scale_hash"],
            criterion_config_hash=components["criterion_config_hash"],
            scale_revision_id=scale_revision_id, knowledge_snapshot_id=snapshot["id"],
            knowledge_revision=snapshot["knowledge_revision"], retrieval_config=retrieval_cfg,
            status="succeeded", per_criterion=result.get("per_criterion") or [],
            payload=shadow_payload, started_at=started, completed_at=completed,
            pair_id=pair_id, primary_run_id=primary_run_id,
            llm_meta=result.get("_llm_meta"))
    except Exception as exc:
        logging.exception("ai-qa: shadow RAG run failed for call %s", call_id)
        completed = runtime_store.now_utc()
        try:
            runtime_store.save_evaluation_run(
                run_id=run_id, call_id=call_id, direction_id=direction_id,
                transcript_cache_id=transcript_cache_id, transcript_hash=transcript_hash,
                evaluation_fingerprint=fingerprint, fingerprint_components=components,
                run_kind="shadow", model=config.CLAUDE_MODEL,
                model_config_hash=content_hash(components["model_config"]),
                prompt_hash=components["prompt_hash"],
                output_schema_hash=components["output_schema_hash"],
                output_schema_version="ai-qa-evaluation-v2", criteria_hash=direction["scale_hash"],
                criterion_config_hash=components["criterion_config_hash"],
                scale_revision_id=scale_revision_id, knowledge_snapshot_id=snapshot["id"],
                knowledge_revision=snapshot["knowledge_revision"], retrieval_config=retrieval_cfg,
                status="failed", per_criterion=[],
                payload={"id": call_id, "_primary_run_id": primary_run_id,
                         "_retrieval_trace": {"status": "degraded", "errors": [str(exc)]}},
                started_at=started, completed_at=completed,
                pair_id=pair_id, primary_run_id=primary_run_id,
                error_code=type(exc).__name__, error_message=str(exc)[:2000])
        except Exception:
            logging.exception("ai-qa: failed to persist shadow failure for call %s", call_id)


def criteria_config_get(direction_id: int) -> dict:
    """Критерии направления с текущим источником оценки (таблица + эвристика)."""
    d = criteria_mod.load_direction(direction_id)
    cc.apply_to_direction(d)
    return {
        "direction_id": d["id"], "name": d["name"],
        "scale_hash": d.get("scale_hash"),
        "criteria": [{"idx": c["idx"], "criterion_id": c.get("criterion_id"), "name": c["name"],
                      "is_critical": bool(c["is_critical"]), "source": c["eval_source"]}
                     for c in d["criteria"]],
    }


def criteria_config_set(direction_id: int, items: list[dict]) -> int:
    """Атомарно сохраняет классификацию текущей шкалы по stable criterion_id."""
    direction = criteria_mod.load_direction(int(direction_id))
    cc.apply_to_direction(direction)
    requested = list(items or [])
    if not requested:
        return 0

    by_idx = {c["idx"]: c for c in direction["criteria"]}
    by_id = {str(c["criterion_id"]): c for c in direction["criteria"]}
    overrides, seen = {}, set()
    for item in requested:
        supplied_id = str(item.get("criterion_id") or "").strip()
        supplied_idx = int(item["criterion_idx"])
        criterion = by_id.get(supplied_id) if supplied_id else by_idx.get(supplied_idx)
        if criterion is None:
            identity = supplied_id or supplied_idx
            raise ValueError(f"критерий {identity} отсутствует в текущей шкале")
        if supplied_id and supplied_idx != int(criterion["idx"]):
            raise ValueError(
                f"criterion_idx {supplied_idx} не соответствует criterion_id {supplied_id!r} "
                "в текущей шкале")
        criterion_id = str(criterion["criterion_id"])
        if criterion_id in seen:
            raise ValueError(f"критерий {criterion_id!r} указан повторно")
        if item.get("eval_source") not in cc.SOURCES:
            raise ValueError(f"неизвестный источник: {item.get('eval_source')}")
        seen.add(criterion_id)
        overrides[criterion_id] = item["eval_source"]

    complete = []
    for criterion in direction["criteria"]:
        criterion_id = str(criterion["criterion_id"])
        source = overrides.get(criterion_id, criterion.get("eval_source"))
        criterion["eval_source"] = source
        complete.append({
            "criterion_idx": int(criterion["idx"]),
            "criterion_id": criterion_id,
            "eval_source": source,
        })

    from .rag import knowledge
    conn = config.connect_rw()
    try:
        scale_revision_id = None
        try:
            scale_revision_id = knowledge.sync_scale_revision(
                conn, direction_id=direction["id"],
                scale_hash=direction.get("scale_hash") or "",
                criteria=direction["criteria"],
            )
        except Exception as exc:
            # During a rolling migration the immutable scale tables may not yet
            # exist.  Data/constraint failures are never treated as compatibility.
            if getattr(exc, "pgcode", None) not in {"42P01", "42703"}:
                raise
            conn.rollback()
            logging.warning("ai-qa: scale revision schema is not available yet; "
                            "saving legacy criterion config")
        cc.replace_config(direction["id"], scale_revision_id, complete, conn=conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(requested)


def adjudications_list(direction=None, q=None, *, status=None, index_status=None,
                       page=1, page_size=20) -> dict:
    """Server-paginated policy catalog with explicit health/degraded states."""
    # Переиндексацию НЕ пинаем из GET-каталога: она дёргается при постановке задачи
    # (queue_reindex_adjudication) и сама себя перезапускает по Timer, пока очередь не
    # опустеет. Иначе каждый показ страницы/буква в поиске открывали бы RW-соединение
    # и платный embedding как побочный эффект чтения (в т.ч. падение на RO-репликах).
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    offset = (page - 1) * page_size
    from .embeddings.provider import configured_contract
    embedding_contract = configured_contract()
    conn = None
    try:
        conn = config.connect_ro(); cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        where, params = ["1=1"], []
        if direction and direction != "all":
            if str(direction).isdigit():
                where.append("c.direction_id=%s"); params.append(int(direction))
            else:
                where.append("c.direction_name=%s"); params.append(direction)
        if status and status != "all":
            where.append("c.rule_status=%s"); params.append(status)
        else:
            # Удалённые (deprecated) скрыты из каталога по умолчанию: у них отдельная
            # вкладка «Удалённые», которая запрашивает их явным status='deprecated'.
            where.append("c.rule_status IS DISTINCT FROM 'deprecated'")
        if index_status and index_status != "all":
            if index_status in ("indexed", "ready"):
                where.append("(c.index_status='ready' AND c.embedding_provider=%s AND "
                             "c.embedding_model=%s AND c.embedding_dim=%s AND "
                             "c.embedding_config_hash=%s)")
                params.extend([embedding_contract["provider"], embedding_contract["model"],
                               embedding_contract["dim"], embedding_contract["config_hash"]])
            elif index_status == "stale":
                where.append("(c.index_status='ready' AND (c.embedding_provider=%s AND "
                             "c.embedding_model=%s AND c.embedding_dim=%s AND "
                             "c.embedding_config_hash=%s) IS NOT TRUE)")
                params.extend([embedding_contract["provider"], embedding_contract["model"],
                               embedding_contract["dim"], embedding_contract["config_hash"]])
            else:
                normalized = "error" if index_status == "failed" else index_status
                where.append("c.index_status=%s"); params.append(normalized)
        if q:
            pattern = f"%{str(q).strip()}%"
            where.append("(c.criterion_name ILIKE %s OR c.situation ILIKE %s OR "
                         "c.excerpt ILIKE %s OR c.reason ILIKE %s OR c.not_covered ILIKE %s)")
            params.extend([pattern] * 5)
        predicate = " AND ".join(where)
        cur.execute(
            f"""SELECT c.rule_id,c.direction_id,c.direction_name,c.criterion_name,
                       c.ai_verdict,c.correct_verdict,c.excerpt,c.reason,c.not_covered,
                       c.situation,c.rule_status,c.index_status,c.embedding_model,
                       c.embedding_provider,c.embedding_dim,c.rule_version,c.content_hash,
                       c.included_count,u.name,c.created_at,c.updated_at,c.index_error,
                       c.verified_excerpt,c.evidence_status,c.rule_version_id,
                       c.embedding_config_hash,COUNT(*) OVER()
                  FROM qa_policy_rule_catalog c
                  LEFT JOIN users u ON u.id=c.created_by
                 WHERE {predicate}
                 ORDER BY c.updated_at DESC,c.rule_id
                 LIMIT %s OFFSET %s""",
            params + [page_size, offset],
        )
        rows = cur.fetchall()
        if rows:
            total = int(rows[0][26])
        else:
            cur.execute(f"SELECT COUNT(*) FROM qa_policy_rule_catalog c WHERE {predicate}", params)
            total = int(cur.fetchone()[0])
        items = [{
            "id": row[0], "direction_id": row[1], "direction": row[2] or "—",
            "criterion": row[3] or "—", "criterion_name": row[3] or "—",
            "ai": row[4], "ai_verdict": row[4], "correct": row[5],
            "correct_verdict": row[5], "excerpt": row[6] or "", "reason": row[7] or "",
            "not_covered": row[8], "situation": row[9], "rule_status": row[10],
            "index_status": ("indexed" if row[11] == "ready" and
                             (row[13], row[12], row[14], row[25]) ==
                             (embedding_contract["provider"], embedding_contract["model"],
                              embedding_contract["dim"], embedding_contract["config_hash"])
                             else ("stale" if row[11] == "ready" else row[11])),
            "embedding_model": row[12], "embedding_provider": row[13],
            "embedding_dim": row[14], "rule_version": row[15], "content_hash": row[16],
            "exposure_count": int(row[17] or 0), "use_count": int(row[17] or 0),
            "by": row[18] or "—", "created_at": row[19], "updated_at": row[20],
            "date": row[19].strftime("%d.%m.%Y") if row[19] else "—",
            "index_error": row[21], "verified_excerpt": bool(row[22]),
            "evidence_status": row[23], "rule_version_id": row[24],
            "embedding_config_hash": row[25],
        } for row in rows]

        facets = {}
        for name, column in (("directions", "direction_name"),
                             ("statuses", "rule_status")):
            cur.execute(
                f"""SELECT {column},COUNT(*) FROM qa_policy_rule_catalog
                      GROUP BY {column} ORDER BY {column} NULLS LAST""")
            facets[name] = [{"value": value or "unknown", "label": value or "—", "count": int(count)}
                            for value, count in cur.fetchall()]
        cur.execute(
            """SELECT CASE
                         WHEN index_status='ready' AND embedding_provider=%s
                          AND embedding_model=%s AND embedding_dim=%s
                          AND embedding_config_hash=%s THEN 'ready'
                         WHEN index_status='ready' THEN 'stale'
                         ELSE coalesce(index_status,'unknown') END AS effective_status,
                      COUNT(*)
                 FROM qa_policy_rule_catalog
                GROUP BY effective_status ORDER BY effective_status""",
            (embedding_contract["provider"], embedding_contract["model"],
             embedding_contract["dim"], embedding_contract["config_hash"]))
        facets["index_statuses"] = [
            {"value": value, "label": value, "count": int(count)}
            for value, count in cur.fetchall()]
        cur.execute(
            """SELECT (SELECT COALESCE(MAX(current_revision),0) FROM qa_knowledge_state),
                      (SELECT COUNT(*) FROM qa_policy_rules WHERE rule_status='active'),
                      (SELECT COUNT(*) FROM qa_policy_rules r
                        JOIN qa_policy_rule_versions v ON v.id=r.current_version_id
                        JOIN qa_policy_rule_embeddings e
                          ON e.rule_version_id=v.id AND e.index_status='ready'
                        JOIN qa_embedding_models m ON m.id=e.embedding_model_id
                       WHERE r.rule_status='active' AND m.embedding_provider=%s
                         AND m.embedding_model=%s AND m.embedding_dim=%s
                         AND m.config_hash=%s),
                      (SELECT MAX(updated_at) FROM qa_knowledge_state),
                      %s""",
            (embedding_contract["provider"], embedding_contract["model"],
             embedding_contract["dim"], embedding_contract["config_hash"],
             embedding_contract["model"]))
        state = cur.fetchone()
        active_count, indexed_count = int(state[1] or 0), int(state[2] or 0)
        degraded = active_count > indexed_count
        health = {"status": "degraded" if degraded else "healthy", "ok": not degraded,
                  "degraded": degraded,
                  "message": (f"{active_count-indexed_count} active rules are not indexed"
                              if degraded else "Knowledge index is ready")}
        knowledge_payload = {
            "revision": int(state[0] or 0), "active_count": active_count,
            "indexed_count": indexed_count, "updated_at": state[3],
            "embedding_model": state[4],
        }
        cur.close(); conn.close(); conn = None
        return {"items": items, "total": total, "page": page, "page_size": page_size,
                "facets": facets, "knowledge": knowledge_payload, "health": health}
    except Exception as exc:
        logging.exception("ai-qa: policy catalog load failed")
        if runtime_store.is_schema_compat_error(exc):
            try:
                return _legacy_adjudications_page(direction=direction, q=q, page=page,
                                                   page_size=page_size)
            except Exception:
                logging.exception("ai-qa: legacy policy catalog fallback failed")
        return {"items": [], "total": 0, "page": page, "page_size": page_size,
                "facets": {"directions": [], "statuses": [], "index_statuses": []},
                "knowledge": {},
                "health": {"status": "error", "ok": False, "degraded": True,
                           "message": "Не удалось загрузить базу знаний",
                           "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _legacy_adjudications_page(*, direction=None, q=None, page=1, page_size=20) -> dict:
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            sql = """SELECT a.id,d.name,a.criterion_name,a.ai_verdict,a.correct_verdict,
                            a.excerpt,a.reason,a.use_count,u.name,a.created_at,
                            a.not_covered,a.situation,COUNT(*) OVER()
                       FROM qa_adjudications a
                       LEFT JOIN directions d ON d.id=a.direction_id
                       LEFT JOIN users u ON u.id=a.created_by
                      WHERE a.is_active"""
            params = []
            if direction and direction != "all":
                sql += " AND d.name=%s"; params.append(direction)
            if q:
                pattern = f"%{q}%"
                sql += " AND (a.criterion_name ILIKE %s OR a.excerpt ILIKE %s OR " \
                       "a.reason ILIKE %s OR a.situation ILIKE %s OR a.not_covered ILIKE %s)"
                params.extend([pattern] * 5)
            sql += " ORDER BY a.created_at DESC LIMIT %s OFFSET %s"
            params.extend([page_size, (page - 1) * page_size])
            cur.execute(sql, params)
            rows = cur.fetchall()
        items = [{"id": f"legacy:{row[0]}", "direction": row[1], "criterion": row[2],
                  "ai": row[3], "correct": row[4], "excerpt": row[5], "reason": row[6],
                  "use_count": int(row[7] or 0), "by": row[8] or "—",
                  "date": row[9].strftime("%d.%m.%Y") if row[9] else "—",
                  "not_covered": row[10], "situation": row[11],
                  "rule_status": "active", "index_status": "indexed"}
                 for row in rows]
        return {"items": items, "total": int(rows[0][12]) if rows else 0,
                "page": page, "page_size": page_size,
                "facets": {"directions": [], "statuses": [], "index_statuses": []},
                "knowledge": {},
                "health": {"status": "degraded", "ok": False, "degraded": True,
                           "message": "Работает legacy-каталог; завершите миграцию схемы"}}
    finally:
        conn.close()


def rag_rollout_get() -> dict:
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT d.id,d.name,coalesce(r.rollout_mode,%s),
                          coalesce(r.canary_percent,%s),r.quality_gates,
                          r.approved_experiment_id::text,r.updated_at
                     FROM directions d
                     LEFT JOIN qa_rag_rollout_config r ON r.direction_id=d.id
                    WHERE d.id=ANY(%s) ORDER BY d.name""",
                (config.RAG_MODE, config.RAG_CANARY_PERCENT, config.OP_DIRECTION_IDS))
            rows = cur.fetchall()
            items = []
            for row in rows:
                approval = _approved_experiment_state(
                    cur, direction_id=row[0], experiment_id=row[5])
                configured_mode = str(row[2])
                gates = row[4] or {}
                manual = gates.get("manual_override") or None
                if configured_mode in ("canary", "active"):
                    approval = _bind_rollout_approval(gates, approval)
                live = approval["valid"] or bool(manual)
                effective_mode = (configured_mode if configured_mode not in ("canary", "active")
                                  or live else "shadow")
                items.append({"direction_id": row[0], "direction": row[1],
                              "mode": configured_mode, "effective_mode": effective_mode,
                              "canary_percent": row[3], "quality_gates": gates,
                              "approved_experiment_id": row[5], "approval_valid": approval["valid"],
                              "approval_reason": approval["reason"], "updated_at": row[6],
                              "manual_override": bool(manual),
                              "manual_reason": (manual or {}).get("reason")})
        return {"items": items}
    finally:
        conn.close()


def rag_rollout_set(*, direction_id: int, mode: str, canary_percent=0,
                    approved_experiment_id=None, manual_override=False,
                    override_reason=None, actor_id=None) -> dict:
    mode = str(mode or "").strip().lower()
    if mode not in ("off", "shadow", "canary", "active"):
        raise ValueError("недопустимый режим RAG rollout")
    direction_id = int(direction_id)
    if direction_id not in set(config.OP_DIRECTION_IDS):
        raise ValueError("направление не входит в AI QA")
    percent = max(0, min(100, int(canary_percent or 0)))
    if mode == "canary" and not (1 <= percent <= 99):
        raise ValueError("для canary укажите процент от 1 до 99")
    if mode == "active":
        percent = 100
    if approved_experiment_id:
        try:
            approved_experiment_id = str(uuid.UUID(str(approved_experiment_id)))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("некорректный ID контрольной проверки") from None
    conn = config.connect_rw()
    try:
        with conn:
            with conn.cursor() as cur:
                gates = {"alarm_precision_gain_pp": 10, "max_recall_drop_pp": 2,
                         "max_false_hit_rate": .05, "max_p95_retrieval_ms": 500,
                         "min_pairs": 30}
                approval = _approved_experiment_state(
                    cur, direction_id=direction_id,
                    experiment_id=approved_experiment_id)
                if mode in ("canary", "active"):
                    if approved_experiment_id and approval["valid"]:
                        gates.update({
                            "approved_knowledge_snapshot_id": approval["approved_snapshot_id"],
                            "approved_evaluation_config_hash": approval["evaluation_config_hash"],
                        })
                    elif manual_override:
                        # Осознанное включение без эксперимента: пишем, кто и почему,
                        # чтобы решение было отслеживаемым и обратимым.
                        gates["manual_override"] = {
                            "by": actor_id,
                            "reason": (str(override_reason or "").strip()
                                       or "ручное включение RAG без paired experiment")[:500],
                            "at": runtime_store.now_utc().isoformat(),
                        }
                    elif not approved_experiment_id:
                        raise ValueError("для включения влияния RAG нужен одобренный paired "
                                         "experiment либо явное ручное включение (manual_override)")
                    else:
                        raise ValueError(f"rollout оставлен в безопасном режиме: {approval['reason']}")
                cur.execute(
                    """INSERT INTO qa_rag_rollout_config
                           (direction_id,rollout_mode,canary_percent,quality_gates,
                            approved_experiment_id,updated_by,updated_at)
                         VALUES (%s,%s,%s,%s,%s,%s,now())
                         ON CONFLICT (direction_id) DO UPDATE SET
                           rollout_mode=EXCLUDED.rollout_mode,
                           canary_percent=EXCLUDED.canary_percent,
                           quality_gates=EXCLUDED.quality_gates,
                           approved_experiment_id=EXCLUDED.approved_experiment_id,
                           updated_by=EXCLUDED.updated_by,updated_at=now()
                         RETURNING updated_at""",
                    (direction_id, mode, percent, Json(gates),
                     approved_experiment_id, actor_id))
                updated_at = cur.fetchone()[0]
        return {"direction_id": direction_id, "mode": mode, "canary_percent": percent,
                "approved_experiment_id": approved_experiment_id,
                "approval_valid": approval["valid"],
                "approval_reason": approval["reason"], "updated_at": updated_at}
    finally:
        conn.close()


def _adjudication_exists(call_id, direction_id, criterion_idx, correct_verdict) -> bool:
    """Повторное сохранение того же разбора (звонок открыли дважды) не должно плодить
    дубли в RAG: одинаковые прецеденты вытесняют из top-K разнообразные."""
    try:
        conn = config.connect_ro(); cur = conn.cursor()
        cur.execute(
            """SELECT 1 FROM qa_adjudications
                WHERE call_id=%s AND direction_id=%s AND criterion_idx=%s AND correct_verdict=%s
                  AND is_active
                LIMIT 1""",
            (call_id, direction_id, criterion_idx, correct_verdict))
        row = cur.fetchone(); cur.close(); conn.close()
        return row is not None
    except Exception:
        return False


def _payload_transcript_text(payload: dict) -> str:
    authoritative_text = payload.get("_authoritative_transcript_text")
    if authoritative_text is not None:
        return str(authoritative_text)
    transcript_cache_id = payload.get("_transcript_cache_id")
    if transcript_cache_id is not None:
        try:
            record = runtime_store.get_transcript_by_id(int(transcript_cache_id))
            if record and record.get("text"):
                return str(record["text"])
        except Exception:
            logging.exception("ai-qa: failed to load authoritative transcript %s",
                              transcript_cache_id)
    asm = payload.get("_asm") or {}
    if asm.get("text"):
        return str(asm["text"])
    lines = []
    for line in payload.get("transcript") or []:
        text = "".join(str(seg.get("t") or "") for seg in (line.get("seg") or []))
        if text.strip():
            lines.append(text.strip())
    return "\n".join(lines)


def _validated_adjudication_items(call_id, direction_id, items, *,
                                  evaluation_run_id=None, scale_revision_id=None,
                                  evaluation_fingerprint=None) -> tuple[dict, list[dict]]:
    """Validate corrections against the exact immutable card shown in the UI."""
    try:
        call_id = int(call_id)
        requested_direction = int(direction_id)
        requested_scale = int(scale_revision_id)
    except (TypeError, ValueError):
        raise ValueError("неверные call_id, direction_id или scale_revision_id")
    if not evaluation_run_id:
        raise ValueError("evaluation_run_id обязателен; переоткройте карточку")
    if not evaluation_fingerprint:
        raise ValueError("evaluation_fingerprint обязателен; переоцените карточку")

    source = runtime_store.get_adjudication_source(str(evaluation_run_id))
    if not source:
        raise ValueError("указанная immutable evaluation_run не найдена")
    if source.get("status") != "succeeded" or source.get("run_kind") not in {
            "standard", "force", "batch"}:
        raise ValueError("evaluation_run не является успешной пользовательской оценкой")
    if not source.get("is_latest"):
        raise ValueError("оценка устарела: звонок уже переоценён; запустите переоценку карточки")
    if source.get("review_outcome"):
        raise ValueError("эта evaluation_run уже проверена")
    if call_id != int(source["call_id"]):
        raise ValueError("call_id не совпадает с evaluation_run")
    if requested_direction != int(source["direction_id"]):
        raise ValueError("направление не совпадает с evaluation_run")
    if source.get("scale_revision_id") is None or requested_scale != int(source["scale_revision_id"]):
        raise ValueError("scale_revision_id не совпадает с evaluation_run")
    if str(evaluation_fingerprint) != str(source["evaluation_fingerprint"]):
        raise ValueError("evaluation_fingerprint не совпадает с evaluation_run")
    if requested_direction not in set(config.OP_DIRECTION_IDS):
        raise ValueError("направление звонка не разрешено для AI QA")

    transcript_record = source.get("transcript")
    if (not transcript_record or not transcript_record.get("text") or
            str(transcript_record.get("transcript_hash")) != str(source.get("transcript_hash")) or
            content_hash(str(transcript_record.get("text"))) != str(source.get("transcript_hash"))):
        raise ValueError("immutable transcript отсутствует или не совпадает с evaluation_run")
    scale_revision = source.get("scale")
    if (not scale_revision or int(scale_revision["id"]) != requested_scale or
            int(scale_revision["direction_id"]) != requested_direction):
        raise ValueError("immutable scale revision отсутствует или не совпадает с evaluation_run")

    payload = dict(source.get("payload") or {})
    embedded_checks = (
        ("_evaluation_run_id", str(source["id"])),
        ("_scale_revision_id", str(requested_scale)),
        ("_evaluation_fingerprint", str(source["evaluation_fingerprint"])),
        ("_transcript_hash", str(source["transcript_hash"])),
    )
    for field, expected in embedded_checks:
        if payload.get(field) is not None and str(payload[field]) != expected:
            raise ValueError(f"повреждённый evaluation_run: поле {field} не совпадает")
    if payload.get("id") is not None and int(payload["id"]) != call_id:
        raise ValueError("повреждённый evaluation_run: call_id в payload не совпадает")
    if (payload.get("direction_id") is not None and
            int(payload["direction_id"]) != requested_direction):
        raise ValueError("повреждённый evaluation_run: direction_id в payload не совпадает")

    payload.update({
        "id": call_id, "direction_id": requested_direction,
        "_evaluation_run_id": str(source["id"]),
        "_scale_revision_id": requested_scale,
        "_evaluation_fingerprint": str(source["evaluation_fingerprint"]),
        "_transcript_cache_id": int(transcript_record["id"]),
        "_transcript_hash": str(source["transcript_hash"]),
        "_authoritative_transcript_text": str(transcript_record["text"]),
        "transcript": transcript_record.get("segments") or [],
        "_evaluation_model": source.get("model"),
    })

    scale_by_id = {}
    scale_by_idx = {}
    for criterion in scale_revision.get("criteria") or []:
        criterion_id = str(criterion.get("criterion_id") or "")
        idx = int(criterion["criterion_idx"])
        if not criterion_id or criterion_id in scale_by_id or idx in scale_by_idx:
            raise ValueError("immutable scale revision содержит неоднозначные критерии")
        scale_by_id[criterion_id] = criterion
        scale_by_idx[idx] = criterion
    if not scale_by_id:
        raise ValueError("immutable scale revision не содержит критериев")

    card_by_id = {}
    card_by_idx = {}
    for card in payload.get("criteria") or []:
        criterion_id = str(card.get("criterion_id") or "")
        try:
            idx = int(card.get("idx"))
        except (TypeError, ValueError):
            raise ValueError("evaluation_run содержит критерий без стабильного idx")
        scale = scale_by_id.get(criterion_id)
        if (not criterion_id or not scale or int(scale["criterion_idx"]) != idx or
                criterion_id in card_by_id or idx in card_by_idx):
            raise ValueError("evaluation_run не совпадает со своей immutable scale revision")
        if (card.get("source") != scale.get("eval_source") or
                str(card.get("name") or "") != str(scale.get("criterion_name") or "")):
            raise ValueError("evaluation_run содержит устаревший источник или имя критерия")
        card_by_id[criterion_id] = card
        card_by_idx[idx] = card
    if set(card_by_id) != set(scale_by_id):
        raise ValueError("evaluation_run содержит неполный набор критериев immutable scale revision")

    transcript = _payload_transcript_text(payload)
    clean_items = []
    seen_ids = set()
    for raw in items or []:
        if not isinstance(raw, dict):
            raise ValueError("каждое исправление должно быть JSON-объектом")
        criterion_id = str(raw.get("criterion_id") or "").strip()
        try:
            idx = int(raw.get("criterion_idx"))
        except (TypeError, ValueError):
            raise ValueError("неверный criterion_idx")
        if not criterion_id:
            raise ValueError(f"criterion_id обязателен для критерия {idx}")
        if criterion_id in seen_ids:
            raise ValueError(f"критерий {criterion_id} передан несколько раз")
        seen_ids.add(criterion_id)
        scale = scale_by_id.get(criterion_id)
        card = card_by_id.get(criterion_id)
        if (not scale or not card or int(scale["criterion_idx"]) != idx or
                int(card["idx"]) != idx):
            raise ValueError(
                f"критерий {criterion_id} / idx {idx} устарел или не совпадает с evaluation_run")
        if card.get("source") != cc.TRANSCRIPT or scale.get("eval_source") != cc.TRANSCRIPT:
            raise ValueError(f"критерий {criterion_id} не оценивается по транскрипту")
        verdict = str(raw.get("correct_verdict") or "").strip()
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"недопустимый вердикт для критерия {criterion_id}")
        if verdict == card.get("ai"):
            raise ValueError(f"критерий {criterion_id} не содержит исправления вердикта")
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"укажите правило/обоснование для критерия {criterion_id}")
        evidence_status, start, end = validate_evidence(
            transcript,
            excerpt=raw.get("excerpt"),
            evidence_status=raw.get("evidence_status"),
            excerpt_verified=bool(raw.get("excerpt_verified")),
        )
        canonical_excerpt = (transcript[start:end] if evidence_status == "verified"
                             and start is not None and end is not None else "")
        clean_items.append({
            "criterion_idx": idx,
            "criterion_id": criterion_id,
            "criterion_name": scale["criterion_name"],
            "ai_verdict": card.get("ai"),
            "correct_verdict": verdict,
            "reason": reason,
            "situation": str(raw.get("situation") or "").strip() or None,
            "not_covered": str(raw.get("not_covered") or "").strip() or None,
            "excerpt": canonical_excerpt.strip(),
            "excerpt_verified": evidence_status == "verified",
            "evidence_status": evidence_status,
            "excerpt_start": start,
            "excerpt_end": end,
        })
    return payload, clean_items


def _evidence_time_range(payload: dict, excerpt: str) -> tuple[int | None, int | None]:
    """Map a verified UI excerpt to the retained ASR token/line time range."""
    if not excerpt:
        return None, None
    text_parts, ranges, position = [], [], 0
    for line_index, line in enumerate(payload.get("transcript") or []):
        if line_index:
            text_parts.append("\n"); position += 1
        line_start_position = position
        for segment in line.get("seg") or []:
            value = str(segment.get("t") or "")
            start, end = position, position + len(value)
            if value:
                ranges.append((start, end, segment.get("start_time_ms", line.get("start_ms")),
                               segment.get("end_time_ms", line.get("end_ms"))))
            text_parts.append(value); position = end
        if position == line_start_position:
            value = str(line.get("text") or "")
            text_parts.append(value)
            ranges.append((position, position + len(value), line.get("start_ms"), line.get("end_ms")))
            position += len(value)
    offsets = locate_excerpt("".join(text_parts), excerpt)
    if not offsets:
        return None, None
    start, end = offsets
    touched = [(start_ms, end_ms) for left, right, start_ms, end_ms in ranges
               if right > start and left < end]
    starts = [int(item[0]) for item in touched if item[0] is not None]
    ends = [int(item[1]) for item in touched if item[1] is not None]
    return (min(starts) if starts else None, max(ends) if ends else None)


def _record_rule_review_feedback(evaluation_run_id, *, corrected_criterion_ids: set[str],
                                 confirmed: bool):
    if not evaluation_run_id:
        return
    try:
        conn = config.connect_rw()
        with conn, conn.cursor() as cur:
            if confirmed:
                cur.execute(
                    """UPDATE qa_policy_rule_metrics m
                          SET review_confirmed_count=review_confirmed_count+1,updated_at=now()
                         FROM (SELECT DISTINCT h.rule_id
                                 FROM qa_retrieval_hits h
                                 JOIN qa_retrieval_runs rr ON rr.id=h.retrieval_run_id
                                WHERE rr.evaluation_run_id=%s AND h.included
                                  AND h.rule_id IS NOT NULL) used
                        WHERE m.rule_id=used.rule_id""", (str(evaluation_run_id),))
            elif corrected_criterion_ids:
                cur.execute(
                    """UPDATE qa_policy_rule_metrics m
                          SET review_corrected_count=review_corrected_count+1,updated_at=now()
                         FROM (SELECT DISTINCT h.rule_id
                                 FROM qa_retrieval_hits h
                                 JOIN qa_retrieval_runs rr ON rr.id=h.retrieval_run_id
                                WHERE rr.evaluation_run_id=%s AND h.included
                                  AND h.rule_id IS NOT NULL
                                  AND h.criterion_id=ANY(%s)) used
                        WHERE m.rule_id=used.rule_id""",
                    (str(evaluation_run_id), list(corrected_criterion_ids)))
        conn.close()
    except Exception:
        logging.exception("ai-qa: failed to record rule review feedback for run %s",
                          evaluation_run_id)


def save_adjudications(call_id, direction_id, items, reviewer_id=None, *,
                       evaluation_run_id=None, scale_revision_id=None,
                       evaluation_fingerprint=None) -> int:
    """Create verified evidence cases and indexed policy-rule drafts atomically."""
    try:
        locked_call_id = int(call_id)
    except (TypeError, ValueError):
        raise ValueError("неверный call_id")
    # The same cross-worker lock is used by refresh/batch publication.  A run
    # therefore cannot become stale between validation and the case commit.
    with runtime_store.distributed_call_lock(locked_call_id) as lock_acquired:
        if not lock_acquired:
            raise RuntimeError("не удалось заблокировать evaluation_run для безопасного разбора")
        return _save_adjudications_locked(
            locked_call_id, direction_id, items, reviewer_id=reviewer_id,
            evaluation_run_id=evaluation_run_id,
            scale_revision_id=scale_revision_id,
            evaluation_fingerprint=evaluation_fingerprint)


def _save_adjudications_locked(call_id, direction_id, items, reviewer_id=None, *,
                               evaluation_run_id=None, scale_revision_id=None,
                               evaluation_fingerprint=None) -> int:
    payload, validated = _validated_adjudication_items(
        call_id, direction_id, items or [], evaluation_run_id=evaluation_run_id,
        scale_revision_id=scale_revision_id,
        evaluation_fingerprint=evaluation_fingerprint)
    authoritative_direction = int(payload["direction_id"])
    authoritative_scale_revision = int(payload["_scale_revision_id"])
    transcript = _payload_transcript_text(payload)
    transcript_hash = str(payload["_transcript_hash"])
    outcome = "adjudicated" if (items or []) else "confirmed"

    # External embedding work happens before the short DB transaction.  A
    # provider outage still creates a visible draft with index_status=error; it
    # can never silently enter active retrieval.
    documents = []
    from .rag import knowledge
    for item in validated:
        documents.append(knowledge.rule_document_text(
            situation=item.get("situation"), excerpt=item.get("excerpt"),
            rule_text=item.get("reason")))
    vectors, embedding_meta, embedding_error = [], None, None
    if documents:
        try:
            from .embeddings.provider import get_provider
            provider = get_provider()
            vectors = provider.embed_document(documents)
            embedding_meta = provider.metadata
        except Exception as exc:
            embedding_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
            embedding_meta = {
                "provider": config.EMBEDDINGS_PROVIDER,
                "model": (config.SELFHOST_EMBED_MODEL if config.EMBEDDINGS_PROVIDER == "selfhost"
                          else config.VERTEX_EMBED_MODEL),
                "dim": config.EMBED_DIM,
            }

    conn = config.connect_rw()
    saved = 0
    try:
        with conn:
            with conn.cursor() as cur:
                _claim_review_outcome(
                    cur, call_id=int(call_id), outcome=outcome,
                    reviewer_id=reviewer_id, payload=payload,
                    model=payload.get("_evaluation_model") or config.CLAUDE_MODEL)
            for index, item in enumerate(validated):
                start_ms, end_ms = _evidence_time_range(payload, item["excerpt"])
                case_id = knowledge.create_adjudication_case(
                    conn, direction_id=authoritative_direction,
                    criterion_id=item["criterion_id"], criterion_idx=item["criterion_idx"],
                    criterion_name=item["criterion_name"],
                    scale_revision_id=authoritative_scale_revision,
                    call_id=int(call_id), evaluation_run_id=payload.get("_evaluation_run_id"),
                    ai_verdict=item["ai_verdict"], correct_verdict=item["correct_verdict"],
                    evidence_excerpt=item["excerpt"], evidence_status=item["evidence_status"],
                    evidence_start_offset=item["excerpt_start"],
                    evidence_end_offset=item["excerpt_end"], evidence_start_ms=start_ms,
                    evidence_end_ms=end_ms, transcript_hash=transcript_hash,
                    situation=item.get("situation"), reason=item["reason"],
                    not_covered=item.get("not_covered"), case_status="verified",
                    created_by=reviewer_id, verified_by=reviewer_id,
                    metadata={"source": "ai_qa_review_v2"})
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT r.id::text FROM qa_policy_rules r
                            JOIN qa_policy_rule_versions v ON v.rule_id=r.id
                           WHERE v.source_case_id=%s LIMIT 1""", (case_id,))
                    existing_rule = cur.fetchone()
                if existing_rule:
                    continue
                rule = knowledge.create_draft_policy_rule(
                    conn, case_id=case_id, direction_id=authoritative_direction,
                    criterion_id=item["criterion_id"], criterion_idx=item["criterion_idx"],
                    criterion_name=item["criterion_name"], situation=item.get("situation"),
                    rule_text=item["reason"], correct_verdict=item["correct_verdict"],
                    not_covered=item.get("not_covered"), excerpt=item["excerpt"] or None,
                    verified_excerpt=item["excerpt_verified"],
                    evidence_status=item["evidence_status"],
                    evidence_start_offset=item["excerpt_start"],
                    evidence_end_offset=item["excerpt_end"], created_by=reviewer_id,
                    metadata={"source": "ai_qa_review_v2"})
                if embedding_error:
                    knowledge.mark_rule_index_error(
                        conn, rule_version_id=rule["rule_version_id"],
                        provider=embedding_meta["provider"], model=embedding_meta["model"],
                        embedding_dim=embedding_meta["dim"], error=embedding_error)
                else:
                    knowledge.record_rule_embedding(
                        conn, rule_version_id=rule["rule_version_id"],
                        provider=embedding_meta["provider"], model=embedding_meta["model"],
                        embedding_dim=embedding_meta["dim"], embedding=vectors[index])
                saved += 1
    finally:
        conn.close()

    _record_rule_review_feedback(
        payload.get("_evaluation_run_id"),
        corrected_criterion_ids={item["criterion_id"] for item in validated},
        confirmed=not validated)
    return saved


def _clean_adjudication_patch(body: dict) -> dict:
    """Патч правки разбора: только разрешённые поля, с валидацией.
    correct_verdict — из фиксированного словаря; reason (правило) не может стать пустым;
    необязательные текстовые поля пустая строка очищает в NULL."""
    from .rag.store import EDITABLE_ADJ_FIELDS
    if not isinstance(body, dict):
        raise ValueError("тело запроса должно быть JSON-объектом")
    patch = {}
    for key in (*EDITABLE_ADJ_FIELDS, "rule_status"):
        if key not in body:
            continue
        val = body[key]
        if val is not None and not isinstance(val, str):
            raise ValueError(f"поле {key} должно быть строкой")
        val = val.strip() if isinstance(val, str) else val
        if key == "rule_status":
            if val not in ("draft", "active", "deprecated", "quarantined"):
                raise ValueError("недопустимый статус правила")
            patch[key] = val
        elif key == "correct_verdict":
            if val not in ("Correct", "Incorrect", "N/A"):
                raise ValueError("недопустимый вердикт")
            patch[key] = val
        elif key == "reason":
            if not val:
                raise ValueError("правило (reason) не может быть пустым")
            patch[key] = val
        else:
            patch[key] = val or None
    if not patch:
        raise ValueError("нет полей для изменения")
    return patch


def _legacy_adjudication_id(value) -> int | None:
    raw = str(value)
    if raw.startswith("legacy:"):
        raw = raw.split(":", 1)[1]
    return int(raw) if raw.isdigit() else None


def _migrate_legacy_adjudication(legacy_id: int, actor_id=None) -> dict:
    """Explicitly turn one reviewed legacy row into a canonical no-evidence draft.

    This is intentionally an administrator action rather than an automatic DDL
    backfill: old rows have no trustworthy transcript offsets.  Their historical
    excerpt is retained only as the applicability situation, never relabelled as
    verified evidence.
    """
    from .rag import knowledge, store
    if actor_id is None:
        raise ValueError("legacy migration requires an authenticated reviewer")

    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT direction_id,criterion_idx,criterion_name,call_id,excerpt,
                          ai_verdict,correct_verdict,reason,not_covered,situation,created_by,
                          canonical_rule_id::text
                     FROM qa_adjudications WHERE id=%s""", (int(legacy_id),))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise KeyError(str(legacy_id))
    if row[11]:
        return {"rule_id": row[11], "rule_status": "draft", "already_migrated": True}

    direction = criteria_mod.load_direction(int(row[0]))
    cc.apply_to_direction(direction)
    criterion = next((item for item in direction["criteria"] if int(item["idx"]) == int(row[1])), None)
    if criterion is None:
        raise ValueError("legacy-критерий отсутствует в текущей шкале; сопоставьте его вручную")
    situation = str(row[9] or row[4] or row[7] or "").strip()
    document = knowledge.rule_document_text(
        situation=situation, excerpt=None, rule_text=row[7])
    embedding_result = embedding_error = None
    try:
        embedding_result = store.embed_document_text(document, strict=True)
    except Exception as exc:
        embedding_error = f"{type(exc).__name__}: {str(exc)[:1000]}"

    conn = config.connect_rw()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT canonical_rule_id::text FROM qa_adjudications WHERE id=%s FOR UPDATE",
                    (int(legacy_id),))
                mapped = cur.fetchone()
                if mapped and mapped[0]:
                    return {"rule_id": mapped[0], "rule_status": "draft",
                            "already_migrated": True}
            knowledge_ctx = knowledge.ensure_knowledge_context(conn, direction=direction,
                                                                created_by=actor_id)
            case_id = knowledge.create_adjudication_case(
                conn, direction_id=direction["id"], criterion_id=criterion["criterion_id"],
                criterion_idx=criterion["idx"], criterion_name=criterion.get("name"),
                scale_revision_id=knowledge_ctx["scale_revision_id"], call_id=row[3],
                ai_verdict=row[5], correct_verdict=row[6], evidence_excerpt="",
                evidence_status="no_evidence", reason=row[7], situation=situation,
                not_covered=row[8], case_status="verified", created_by=actor_id or row[10],
                verified_by=actor_id, legacy_adjudication_id=int(legacy_id),
                metadata={"source": "legacy_admin_migration_v2"},
            )
            rule = knowledge.create_draft_policy_rule(
                conn, case_id=case_id, direction_id=direction["id"],
                criterion_id=criterion["criterion_id"], criterion_idx=criterion["idx"],
                criterion_name=criterion.get("name"), situation=situation,
                rule_text=row[7], correct_verdict=row[6], not_covered=row[8], excerpt=None,
                verified_excerpt=False, evidence_status="no_evidence",
                created_by=actor_id or row[10], metadata={"source": "legacy_admin_migration_v2"},
            )
            if embedding_result:
                meta = embedding_result["provider"]
                knowledge.record_rule_embedding(
                    conn, rule_version_id=rule["rule_version_id"], provider=meta["provider"],
                    model=meta["model"], embedding_dim=meta["dim"],
                    embedding=embedding_result["vector"])
            else:
                provider = config.EMBEDDINGS_PROVIDER
                model = (config.SELFHOST_EMBED_MODEL if provider == "selfhost"
                         else config.VERTEX_EMBED_MODEL)
                knowledge.mark_rule_index_error(
                    conn, rule_version_id=rule["rule_version_id"], provider=provider,
                    model=model, embedding_dim=config.EMBED_DIM,
                    error=embedding_error or "embedding unavailable")
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE qa_adjudications
                          SET canonical_case_id=%s,canonical_rule_id=%s,updated_by=%s,updated_at=now()
                        WHERE id=%s""",
                    (case_id, rule["rule_id"], actor_id, int(legacy_id)))
        return {"rule_id": rule["rule_id"], "rule_status": "draft",
                "index_status": "ready" if embedding_result else "error"}
    finally:
        conn.close()


def _canonical_rule_source(rule_id: str) -> dict | None:
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.direction_id,r.rule_status,r.current_version_id,v.situation,
                          v.rule_text,v.not_covered,v.correct_verdict,v.excerpt,
                          v.verified_excerpt,v.evidence_status,v.evidence_start_offset,
                          v.evidence_end_offset,v.content_hash::text
                     FROM qa_policy_rules r
                     JOIN qa_policy_rule_versions v ON v.id=r.current_version_id
                    WHERE r.id=%s""", (str(rule_id),))
            row = cur.fetchone()
            if not row:
                return None
            keys = ("direction_id", "rule_status", "rule_version_id", "situation",
                    "rule_text", "not_covered", "correct_verdict", "excerpt",
                    "verified_excerpt", "evidence_status", "evidence_start_offset",
                    "evidence_end_offset", "content_hash")
            return dict(zip(keys, row))
    finally:
        conn.close()


def update_adjudication(adj_id, body: dict, actor_id=None) -> bool:
    """Immutable canonical revision + optional lifecycle transition."""
    from .rag import knowledge, store
    if not isinstance(body, dict):
        raise ValueError("тело запроса должно быть JSON-объектом")
    expected_version_id = body.get("expected_rule_version_id")
    expected_content_hash = str(body.get("expected_content_hash") or "").strip()
    patch = _clean_adjudication_patch(body)
    legacy_id = _legacy_adjudication_id(adj_id)
    target_status = patch.pop("rule_status", None)
    if legacy_id is not None:
        if target_status == "deprecated":
            return store.delete_adjudication(legacy_id)
        if target_status == "draft":
            try:
                migrated = _migrate_legacy_adjudication(legacy_id, actor_id=actor_id)
            except KeyError:
                return False
            if patch:
                migrated_source = _canonical_rule_source(migrated["rule_id"])
                return update_adjudication(migrated["rule_id"], {
                    **patch,
                    "expected_rule_version_id": migrated_source["rule_version_id"],
                    "expected_content_hash": migrated_source["content_hash"],
                }, actor_id=actor_id)
            return True
        if target_status == "active":
            raise ValueError(
                "сначала переведите legacy-разбор в draft: это явно мигрирует его как no_evidence; затем активируйте canonical-правило")
        if target_status not in (None, "quarantined"):
            raise ValueError("legacy rule must be migrated before this lifecycle transition")
        return store.update_adjudication(legacy_id, patch) if patch else True

    source = _canonical_rule_source(str(adj_id))
    if source is None:
        return False
    if expected_version_id is None or not expected_content_hash:
        raise ValueError("версия правила не передана; обновите каталог и повторите")
    try:
        version_matches = int(expected_version_id) == int(source["rule_version_id"])
    except (TypeError, ValueError):
        raise ValueError("некорректная версия правила") from None
    if not version_matches or expected_content_hash != str(source["content_hash"]):
        raise knowledge.KnowledgeConflict(
            "правило уже изменено другим пользователем; обновите каталог и повторите")
    version_changes = dict(patch)
    version_changes.pop("situation_tag", None)  # legacy-only field
    if "reason" in version_changes:
        version_changes["rule_text"] = version_changes.pop("reason")
    version_changes = {key: value for key, value in version_changes.items()
                       if source.get(key) != value}
    embedding_result, embedding_error = None, None
    if version_changes:
        merged = {**source, **version_changes}
        document = knowledge.rule_document_text(
            situation=merged.get("situation"), excerpt=merged.get("excerpt"),
            rule_text=merged.get("rule_text"))
        try:
            from .rag.store import embed_document_text
            embedding_result = embed_document_text(document, strict=True)
        except Exception as exc:
            embedding_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
    if version_changes and embedding_error and source["rule_status"] == "active":
        raise store.AdjudicationEmbeddingUnavailable(
            "embedding недоступен; активная версия и её рабочий индекс оставлены без изменений")

    activation_blocked = False
    conn = config.connect_rw()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id FROM qa_scale_revisions WHERE direction_id=%s
                        ORDER BY scale_revision DESC LIMIT 1""", (source["direction_id"],))
                scale_row = cur.fetchone()
            scale_revision_id = int(scale_row[0]) if scale_row else None
            current_status = source["rule_status"]
            version_id = int(source["rule_version_id"])
            if version_changes:
                revised = knowledge.revise_policy_rule(
                    conn, rule_id=str(adj_id), changes=version_changes, actor_id=actor_id,
                    reason="edited in RAG administration",
                    expected_version_id=source["rule_version_id"],
                    publish_snapshot=not (target_status == "active"))
                version_id = revised["rule_version_id"]
                current_status = "draft"
                if embedding_result:
                    meta = embedding_result["provider"]
                    knowledge.record_rule_embedding(
                        conn, rule_version_id=version_id, provider=meta["provider"],
                        model=meta["model"], embedding_dim=meta["dim"],
                        embedding=embedding_result["vector"])
                else:
                    meta = {"provider": config.EMBEDDINGS_PROVIDER,
                            "model": (config.SELFHOST_EMBED_MODEL
                                      if config.EMBEDDINGS_PROVIDER == "selfhost"
                                      else config.VERTEX_EMBED_MODEL),
                            "dim": config.EMBED_DIM}
                    knowledge.mark_rule_index_error(
                        conn, rule_version_id=version_id, provider=meta["provider"],
                        model=meta["model"], embedding_dim=meta["dim"],
                        error=embedding_error or "embedding unavailable")
                    if target_status == "active":
                        activation_blocked = True
                        target_status = None
            if target_status == "active" and current_status in ("deprecated", "quarantined"):
                knowledge.transition_policy_rule(
                    conn, rule_id=str(adj_id), to_status="draft", actor_id=actor_id,
                    reason="returned to draft before activation",
                    expected_status=current_status, version_id=version_id,
                    scale_revision_id=scale_revision_id, expected_version_id=version_id)
                current_status = "draft"
            if target_status is not None and target_status != current_status:
                knowledge.transition_policy_rule(
                    conn, rule_id=str(adj_id), to_status=target_status, actor_id=actor_id,
                    reason="lifecycle changed in RAG administration",
                    expected_status=current_status, version_id=version_id,
                    scale_revision_id=scale_revision_id, expected_version_id=version_id)
        if activation_blocked:
            raise store.AdjudicationEmbeddingUnavailable(
                "изменения сохранены как draft, но embedding недоступен; выполните reindex и активируйте правило")
        return True
    finally:
        conn.close()


def delete_adjudication(adj_id, actor_id=None) -> bool:
    """Soft-deprecate a rule while retaining every version and audit event."""
    from .rag import knowledge, store
    legacy_id = _legacy_adjudication_id(adj_id)
    if legacy_id is not None:
        return store.delete_adjudication(legacy_id)
    source = _canonical_rule_source(str(adj_id))
    if source is None:
        return False
    conn = config.connect_rw()
    try:
        with conn:
            knowledge.transition_policy_rule(
                conn, rule_id=str(adj_id), to_status="deprecated", actor_id=actor_id,
                reason="deprecated in RAG administration",
                expected_status=source["rule_status"],
                version_id=source["rule_version_id"],
                expected_version_id=source["rule_version_id"])
        return True
    finally:
        conn.close()


def reindex_adjudication(adj_id, actor_id=None) -> dict:
    """Synchronously rebuild the current rule-version embedding, with visible status."""
    from .rag import knowledge, store
    if _legacy_adjudication_id(adj_id) is not None:
        raise ValueError("сначала мигрируйте legacy-разбор в версионируемое правило")
    source = _canonical_rule_source(str(adj_id))
    if source is None:
        raise KeyError(str(adj_id))
    from .embeddings.provider import configured_contract
    configured_meta = configured_contract()
    check = config.connect_ro()
    try:
        with check.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM qa_policy_rule_embeddings e
                    JOIN qa_embedding_models m ON m.id=e.embedding_model_id
                   WHERE e.rule_version_id=%s AND e.index_status='ready'
                     AND m.embedding_provider=%s AND m.embedding_model=%s
                     AND m.embedding_dim=%s AND m.config_hash=%s LIMIT 1""",
                (source["rule_version_id"], configured_meta["provider"],
                 configured_meta["model"], configured_meta["dim"],
                 configured_meta["config_hash"]))
            if cur.fetchone():
                return {"rule_id": str(adj_id), "index_status": "indexed",
                        "embedding_model": configured_meta["model"],
                        "embedding_dim": configured_meta["dim"], "already_ready": True}
    finally:
        check.close()
    document = knowledge.rule_document_text(
        situation=source.get("situation"), excerpt=source.get("excerpt"),
        rule_text=source.get("rule_text"))
    try:
        embedded = store.embed_document_text(document, strict=True)
    except Exception as exc:
        meta = configured_meta
        conn = config.connect_rw()
        try:
            with conn:
                knowledge.mark_rule_index_error(
                    conn, rule_version_id=source["rule_version_id"],
                    provider=meta["provider"], model=meta["model"],
                    embedding_dim=meta["dim"], error=str(exc))
        finally:
            conn.close()
        raise store.AdjudicationEmbeddingUnavailable(str(exc)) from exc
    meta = embedded["provider"]
    conn = config.connect_rw()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT direction_id,rule_status,current_version_id
                         FROM qa_policy_rules WHERE id=%s FOR UPDATE""", (str(adj_id),))
                live = cur.fetchone()
            if not live or int(live[2]) != int(source["rule_version_id"]):
                raise knowledge.KnowledgeConflict(
                    "rule version changed while its embedding was being computed")
            model_id = knowledge.ensure_embedding_model(
                conn, provider=meta["provider"], model=meta["model"],
                embedding_dim=meta["dim"], config=meta.get("config"))
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id,index_status FROM qa_policy_rule_embeddings
                        WHERE rule_version_id=%s AND embedding_model_id=%s""",
                    (source["rule_version_id"], model_id))
                existing = cur.fetchone()
            if not existing or existing[1] != "ready":
                knowledge.record_rule_embedding(
                    conn, rule_version_id=source["rule_version_id"],
                    provider=meta["provider"], model=meta["model"],
                    embedding_dim=meta["dim"], embedding=embedded["vector"],
                    config=meta.get("config"))
            if live[1] == "active":
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id FROM qa_scale_revisions WHERE direction_id=%s
                            ORDER BY scale_revision DESC LIMIT 1""", (int(live[0]),))
                    scale = cur.fetchone()
                if not scale:
                    raise knowledge.KnowledgeValidationError(
                        "cannot publish reindexed active rule without a scale revision")
                knowledge.create_knowledge_snapshot(
                    conn, direction_id=int(live[0]), scale_revision_id=int(scale[0]),
                    created_by=actor_id, reason="active rule reindexed")
        return {"rule_id": str(adj_id), "index_status": "indexed",
                "embedding_model": meta["model"], "embedding_dim": meta["dim"]}
    finally:
        conn.close()


def queue_reindex_adjudication(adj_id, actor_id=None) -> dict:
    """Persist an idempotent reindex job; the in-process worker is only an accelerator."""
    from .rag import knowledge
    from .embeddings.provider import configured_contract
    rule_id = str(adj_id)
    if _legacy_adjudication_id(rule_id) is not None:
        raise ValueError("сначала мигрируйте legacy-разбор в версионируемое правило")
    source = _canonical_rule_source(rule_id)
    if source is None:
        raise KeyError(rule_id)
    meta = configured_contract()
    job_id = None
    conn = config.connect_rw()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT current_version_id FROM qa_policy_rules
                        WHERE id=%s FOR UPDATE""", (rule_id,))
                live = cur.fetchone()
            if not live:
                raise KeyError(rule_id)
            if int(live[0]) != int(source["rule_version_id"]):
                raise knowledge.KnowledgeConflict("rule version changed before reindex was queued")
            model_id = knowledge.ensure_embedding_model(
                conn, provider=meta["provider"], model=meta["model"],
                embedding_dim=meta["dim"], config=meta["config"])
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT index_status FROM qa_policy_rule_embeddings
                        WHERE rule_version_id=%s AND embedding_model_id=%s""",
                    (source["rule_version_id"], model_id))
                row = cur.fetchone()
            if row and row[0] == "ready":
                return {"rule_id": rule_id, "index_status": "indexed", "already_ready": True}
            knowledge.mark_rule_index_pending(
                conn, rule_version_id=source["rule_version_id"],
                provider=meta["provider"], model=meta["model"], embedding_dim=meta["dim"],
                config=meta["config"])
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id::text FROM qa_reindex_jobs
                        WHERE rule_version_id=%s AND embedding_model_id=%s
                          AND job_status IN ('queued','running')
                        ORDER BY created_at DESC LIMIT 1""",
                    (source["rule_version_id"], model_id))
                existing_job = cur.fetchone()
                if existing_job:
                    job_id = existing_job[0]
                else:
                    cur.execute(
                        """INSERT INTO qa_reindex_jobs
                               (rule_id,rule_version_id,embedding_model_id,requested_by)
                             VALUES (%s,%s,%s,%s) RETURNING id::text""",
                        (rule_id, source["rule_version_id"], model_id, actor_id))
                    job_id = cur.fetchone()[0]
    finally:
        conn.close()
    _kick_reindex_worker()
    return {"rule_id": rule_id, "job_id": job_id, "index_status": "pending",
            "queued": True}


def _claim_reindex_job() -> dict | None:
    """Claim one durable job across all web/worker processes."""
    conn = config.connect_rw()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE qa_reindex_jobs
                          SET job_status='queued',locked_at=NULL,locked_by=NULL,updated_at=now(),
                              last_error=coalesce(last_error,'') || ' [worker lease expired]'
                        WHERE job_status='running' AND locked_at < now()-interval '20 minutes'""")
                cur.execute(
                    """SELECT id::text,rule_id::text,requested_by,attempts
                         FROM qa_reindex_jobs
                        WHERE job_status='queued' AND available_at<=now()
                        ORDER BY available_at,created_at
                        FOR UPDATE SKIP LOCKED LIMIT 1""")
                row = cur.fetchone()
                if not row:
                    return None
                worker = f"pid:{os.getpid()}"
                cur.execute(
                    """UPDATE qa_reindex_jobs
                          SET job_status='running',attempts=attempts+1,locked_at=now(),
                              locked_by=%s,updated_at=now()
                        WHERE id=%s""", (worker, row[0]))
                return {"id": row[0], "rule_id": row[1], "requested_by": row[2],
                        "attempts": int(row[3]) + 1}
    finally:
        conn.close()


def _finish_reindex_job(job: dict, error: Exception | None = None) -> None:
    conn = config.connect_rw()
    try:
        with conn:
            with conn.cursor() as cur:
                if error is None:
                    cur.execute(
                        """UPDATE qa_reindex_jobs
                              SET job_status='succeeded',completed_at=now(),updated_at=now(),
                                  locked_at=NULL,locked_by=NULL,last_error=NULL
                            WHERE id=%s""", (job["id"],))
                elif job["attempts"] >= config.RAG_REINDEX_MAX_ATTEMPTS:
                    cur.execute(
                        """UPDATE qa_reindex_jobs
                              SET job_status='failed',completed_at=now(),updated_at=now(),
                                  locked_at=NULL,locked_by=NULL,last_error=%s
                            WHERE id=%s""", (str(error)[:2000], job["id"]))
                else:
                    delay_seconds = min(1800, 30 * (2 ** max(0, job["attempts"] - 1)))
                    cur.execute(
                        """UPDATE qa_reindex_jobs
                              SET job_status='queued',available_at=now()+(%s*interval '1 second'),
                                  updated_at=now(),locked_at=NULL,locked_by=NULL,last_error=%s
                            WHERE id=%s""",
                        (delay_seconds, str(error)[:2000], job["id"]))
    finally:
        conn.close()


def _drain_reindex_jobs() -> None:
    try:
        while True:
            job = _claim_reindex_job()
            if not job:
                return
            try:
                reindex_adjudication(job["rule_id"], actor_id=job["requested_by"])
            except Exception as exc:
                logging.exception("ai-qa: durable reindex job %s failed", job["id"])
                _finish_reindex_job(job, exc)
                if job["attempts"] < config.RAG_REINDEX_MAX_ATTEMPTS:
                    delay = min(1800, 30 * (2 ** max(0, job["attempts"] - 1)))
                    timer = threading.Timer(delay, _kick_reindex_worker)
                    timer.daemon = True
                    timer.start()
            else:
                _finish_reindex_job(job)
    except Exception:
        logging.exception("ai-qa: durable reindex worker unavailable")
    finally:
        with _reindex_guard:
            _reindex_inflight.discard("durable-worker")


def _kick_reindex_worker() -> None:
    with _reindex_guard:
        if "durable-worker" in _reindex_inflight:
            return
        _reindex_inflight.add("durable-worker")
    _reindex_executor.submit(_drain_reindex_jobs)


def refine_adjudication(body: dict) -> dict:
    """ИИ-подсказка формулировки разбора (человек редактирует и сохраняет сам)."""
    from .rag import refine as rag_refine
    return rag_refine.refine_adjudication(
        direction_id=body["direction_id"], criterion_idx=body["criterion_idx"],
        criterion_name=body.get("criterion_name"),
        ai_verdict=body.get("ai_verdict"), ai_comment=body.get("ai_comment"),
        correct_verdict=body["correct_verdict"], reason=body.get("reason", ""),
        excerpt=body.get("excerpt"))


def random_call() -> dict:
    """Случайный оценённый человеком звонок ОП с записью — для теста ИИ-оценки.
    Сначала из ЕЩЁ НЕ оценённых ИИ (каждый вызов = новый сигнал за те же деньги);
    если все уже оценены — любой."""
    conn = config.connect_ro()
    cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
    base = """SELECT c.id, d.name, u.name, TO_CHAR(c.created_at,'DD.MM HH24:MI'), c.score
                FROM calls c
                LEFT JOIN directions d ON c.direction_id = d.id
                LEFT JOIN users u ON c.operator_id = u.id
               WHERE c.direction_id = ANY(%s) AND c.audio_path IS NOT NULL AND c.audio_path <> ''
                 AND COALESCE(c.is_draft, FALSE) = FALSE AND c.score IS NOT NULL"""
    cur.execute(base + """ AND NOT EXISTS (SELECT 1 FROM ai_review_cache rc
                                            WHERE rc.call_id = c.id AND rc.model = %s)
                           ORDER BY random() LIMIT 1""",
                (config.OP_DIRECTION_IDS, config.CLAUDE_MODEL))
    row = cur.fetchone()
    if not row:
        cur.execute(base + " ORDER BY random() LIMIT 1", (config.OP_DIRECTION_IDS,))
        row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        raise ValueError("нет оценённых звонков ОП с записью")
    return {"id": row[0], "direction": row[1], "operator": row[2] or "—",
            "datetime": row[3], "human_score": row[4]}


def evaluations_list(limit=100) -> list[dict]:
    """Уже оценённые ИИ звонки (из кэша) — реальные данные, пусто пока ничего не оценено.
    Один звонок = одна строка (последняя оценка), иначе звонки, оценённые несколькими
    версиями модели, дублировались в списке."""
    conn = None
    try:
        conn = config.connect_ro()
        cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT t.call_id, d.name, u.name, TO_CHAR(t.created_at,'DD.MM HH24:MI'), c.score, t.ai
                 FROM (SELECT DISTINCT ON (rc.call_id) rc.call_id, rc.created_at,
                              rc.payload->>'ai_score' AS ai
                         FROM ai_review_cache rc
                        ORDER BY rc.call_id, rc.created_at DESC) t
                 JOIN calls c ON c.id = t.call_id
                 LEFT JOIN directions d ON c.direction_id = d.id
                 LEFT JOIN users u ON c.operator_id = u.id
                ORDER BY t.created_at DESC LIMIT %s""", (limit,))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [{"id": r[0], "direction": r[1], "operator": r[2] or "—",
                 "datetime": r[3], "human": r[4],
                 "ai": round(float(r[5])) if r[5] is not None else None} for r in rows]
    except Exception:
        logging.exception("ai-qa evaluations failed")
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


_VERDICTS = ("Correct", "Incorrect", "N/A")


def _rate(hits, total):
    """{pct, hits, total} или None, если событий не было (не рисуем выдуманный 0%)."""
    return {"pct": round(100 * hits / total), "hits": hits, "total": total} if total else None


def _verdict_metrics(rows) -> dict:
    """Метрики доверия по «сырому» эталону — человеческим оценкам calls.scores.

    rows: (criteria, scores, direction), criteria — в форме карточки ревью.
    Считает матрицу «человек × ИИ» и три главных вопроса:
      alarm_precision — когда ИИ ставит Incorrect, как часто человек согласен;
      recall          — какую долю человеческих нарушений ИИ поймал;
      correct_reliability — когда ИИ ставит Correct, как часто человек согласен.
    «Deficiency» (частичный зачёт) НЕ считается расхождением — у ИИ такого
    вердикта пока нет; учитывается отдельным счётчиком."""
    matrix = {h: {a: 0 for a in _VERDICTS} for h in _VERDICTS}
    per = {}
    deficiency = 0
    for criteria, scores, direction in rows:
        for crit in criteria or []:
            if crit.get("source") != "transcript":
                continue
            idx, ai = crit.get("idx"), crit.get("ai")
            if ai not in _VERDICTS:
                continue  # Pending/сбой — не вердикт
            raw = scores[idx] if isinstance(scores, list) and idx is not None and idx < len(scores) else None
            hv = _norm_verdict(raw)
            if hv == "Deficiency":
                deficiency += 1
                continue
            if hv not in _VERDICTS:
                continue
            matrix[hv][ai] += 1
            d = per.setdefault((direction or "—", crit.get("name") or f"#{idx}"),
                               {"n": 0, "match": 0, "alarms": 0, "alarm_hits": 0, "misses": 0})
            d["n"] += 1
            if hv == ai:
                d["match"] += 1
            if ai == "Incorrect":
                d["alarms"] += 1
                if hv == "Incorrect":
                    d["alarm_hits"] += 1
            elif hv == "Incorrect":
                d["misses"] += 1  # человек видит нарушение, ИИ — нет

    tot = sum(matrix[h][a] for h in _VERDICTS for a in _VERDICTS)
    by_criterion = [
        {"direction": dr, "name": nm, "n": d["n"], "v": round(100 * d["match"] / d["n"]),
         "alarms": d["alarms"], "alarm_hits": d["alarm_hits"],
         "false_alarms": d["alarms"] - d["alarm_hits"], "misses": d["misses"]}
        for (dr, nm), d in per.items()]
    return {
        "total": tot,
        "agreement": round(100 * sum(matrix[v][v] for v in _VERDICTS) / tot) if tot else None,
        "alarm_precision": _rate(matrix["Incorrect"]["Incorrect"],
                                 sum(matrix[h]["Incorrect"] for h in _VERDICTS)),
        "recall": _rate(matrix["Incorrect"]["Incorrect"],
                        sum(matrix["Incorrect"][a] for a in _VERDICTS)),
        "correct_reliability": _rate(matrix["Correct"]["Correct"],
                                     sum(matrix[h]["Correct"] for h in _VERDICTS)),
        "matrix": matrix,
        "deficiency": deficiency,
        "by_criterion": by_criterion,
    }


def _reviewed_metrics(cur):
    """«Чистый» эталон: только звонки, где человек нажал «Подтвердить»/«Сохранить разбор».
    confirmed — все вердикты ИИ одобрены; adjudicated — исправленные критерии берём из
    qa_adjudications, неисправленные считаются одобренными. None — миграции меты ещё нет."""
    try:
        cur.execute("""SELECT call_id, review_outcome, per_criterion
                         FROM ai_evaluation_meta
                        WHERE review_outcome IS NOT NULL AND model = %s""",
                    (config.CLAUDE_MODEL,))
        rows = cur.fetchall()
    except Exception:
        return None  # колонок ещё нет — появятся после деплоя (миграция на старте)
    out = {"confirmed": 0, "adjudicated": 0, "endorsed": 0, "corrected": 0, "alarm_precision": None}
    if not rows:
        return out
    corr = {}
    try:
        # Исправления могут быть и от ревью до появления следа — ключ call+criterion этого не различает,
        # для точности тревог это безопасно (исправление = человек не согласен).
        # Деактивированные RAG-правила остаются следом ревью. Если по тому же критерию
        # разбор создавали повторно, человеческим эталоном считаем самый свежий.
        cur.execute("""SELECT DISTINCT ON (call_id, criterion_idx)
                              call_id, criterion_idx, correct_verdict
                         FROM (
                              SELECT call_id,criterion_idx,correct_verdict,created_at,id::text AS id
                                FROM qa_adjudication_cases
                               WHERE case_status='verified' AND call_id=ANY(%s)
                              UNION ALL
                              SELECT call_id,criterion_idx,correct_verdict,created_at,id::text
                                FROM qa_adjudications
                               WHERE call_id=ANY(%s)
                         ) corrections
                        ORDER BY call_id, criterion_idx, created_at DESC, id DESC""",
                    ([r[0] for r in rows], [r[0] for r in rows]))
        corr = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    except Exception:
        pass
    alarms = alarm_hits = 0
    for call_id, outcome, crits in rows:
        out["confirmed" if outcome == "confirmed" else "adjudicated"] += 1
        for c in crits or []:
            if c.get("source") != "transcript" or c.get("ai") not in _VERDICTS:
                continue
            # confirmed означает, что в ТЕКУЩЕМ ревью человек одобрил все verdict'ы ИИ;
            # старые разборы того же звонка не должны превращать это ревью в исправленное.
            hv = (c.get("ai") if outcome == "confirmed"
                  else corr.get((call_id, c.get("idx")), c.get("ai")))
            out["endorsed" if hv == c.get("ai") else "corrected"] += 1
            if c.get("ai") == "Incorrect":
                alarms += 1
                if hv == "Incorrect":
                    alarm_hits += 1
    out["alarm_precision"] = _rate(alarm_hits, alarms)
    return out


def _rag_observability(cur) -> dict:
    """Operational SLOs plus the latest leakage-safe paired experiment report."""
    out = {"status": "ready", "runs": 0, "degraded_runs": 0, "no_match_runs": 0,
           "retrieved": 0, "included": 0, "retrieval_p50_ms": None,
           "retrieval_p95_ms": None, "stale_evaluations": 0,
           "rollout": [], "experiment": None}
    cur.execute(
        """SELECT COUNT(*),COUNT(*) FILTER (WHERE status='failed'),
                  COUNT(*) FILTER (WHERE status='succeeded' AND included_count=0),
                  COALESCE(SUM(candidate_count),0),COALESCE(SUM(included_count),0),
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms),
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
             FROM qa_retrieval_runs WHERE created_at>now()-interval '30 days'""")
    row = cur.fetchone()
    out.update({"runs": int(row[0] or 0), "degraded_runs": int(row[1] or 0),
                "no_match_runs": int(row[2] or 0), "retrieved": int(row[3] or 0),
                "included": int(row[4] or 0),
                "retrieval_p50_ms": round(float(row[5]), 1) if row[5] is not None else None,
                "retrieval_p95_ms": round(float(row[6]), 1) if row[6] is not None else None})
    cur.execute(
        """SELECT COUNT(*)
             FROM ai_evaluation_runs e
             JOIN qa_knowledge_state s ON s.direction_id=e.direction_id
                                      AND s.scale_revision_id=e.scale_revision_id
            WHERE e.status='succeeded' AND e.run_kind IN ('standard','force')
              AND COALESCE((e.retrieval_config->>'enabled')::boolean,false)
              AND e.knowledge_snapshot_id IS DISTINCT FROM s.current_snapshot_id""")
    out["stale_evaluations"] = int(cur.fetchone()[0] or 0)
    cur.execute(
        """SELECT direction_id,rollout_mode,canary_percent,quality_gates,
                  approved_experiment_id::text,updated_at
             FROM qa_rag_rollout_config ORDER BY direction_id""")
    rollout_rows = cur.fetchall()
    for row in rollout_rows:
        approval = _approved_experiment_state(
            cur, direction_id=row[0], experiment_id=row[4])
        configured_mode = str(row[1])
        if configured_mode in ("canary", "active"):
            approval = _bind_rollout_approval(row[3] or {}, approval)
        effective_mode = (configured_mode if configured_mode not in ("canary", "active")
                          or approval["valid"] else "shadow")
        out["rollout"].append({
            "direction_id": row[0], "mode": effective_mode,
            "configured_mode": configured_mode, "canary_percent": row[2],
            "quality_gates": row[3] or {}, "approved_experiment_id": row[4],
            "approval_valid": approval["valid"], "approval_reason": approval["reason"],
            "updated_at": row[5],
        })
    if out["degraded_runs"]:
        out["status"] = "degraded"
    try:
        cur.execute(
            """SELECT x.id::text,x.metrics,x.status,x.completed_at
                 FROM qa_rag_experiments x
                WHERE x.status='succeeded' ORDER BY x.completed_at DESC NULLS LAST LIMIT 1""")
        experiment = cur.fetchone()
        if experiment:
            out["experiment"] = {"id": experiment[0], "metrics": experiment[1] or {},
                                 "status": experiment[2], "completed_at": experiment[3]}
    except Exception:
        pass
    return out


def stats() -> dict:
    """Метрики доверия для дашборда. Два эталона: «сырой» (человеческие оценки из
    calls.scores — много данных, но Correct в форме — дефолт) и «чистый» (итоги ревью —
    мало, но человек реально смотрел). Пустые места — честно null/[], без выдуманных цифр."""
    out = {"queue": 0, "evaluated": 0, "agreement": None, "by_criterion": [], "focus": [],
           "alarm_precision": None, "recall": None, "correct_reliability": None,
           "matrix": None, "deficiency": 0, "reviewed": None, "rag": None}
    conn = None
    try:
        conn = config.connect_ro()
        cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute("SELECT COUNT(*) FROM ai_review_cache")
        out["evaluated"] = cur.fetchone()[0]
        try:  # реальный размер очереди ревью; до миграции — свежие звонки, как раньше
            cur.execute("""SELECT COUNT(*) FROM ai_review_cache rc
                             LEFT JOIN ai_evaluation_meta m ON m.call_id = rc.call_id AND m.model = rc.model
                            WHERE rc.model = %s AND m.review_outcome IS NULL""", (config.CLAUDE_MODEL,))
            out["queue"] = cur.fetchone()[0]
        except Exception:
            cur.execute(
                """SELECT COUNT(*) FROM calls
                    WHERE direction_id = ANY(%s) AND audio_path IS NOT NULL AND audio_path <> ''
                      AND COALESCE(is_draft, FALSE) = FALSE AND created_at > NOW() - INTERVAL '7 days'""",
                (config.OP_DIRECTION_IDS,))
            out["queue"] = cur.fetchone()[0]

        # Сырой эталон: последняя оценка каждого звонка (без дублей по тегам моделей).
        cur.execute(
            """SELECT t.criteria, c.scores, t.direction
                 FROM (SELECT DISTINCT ON (rc.call_id) rc.call_id,
                              rc.payload->'criteria' AS criteria,
                              rc.payload->>'direction' AS direction
                         FROM ai_review_cache rc
                        ORDER BY rc.call_id, rc.created_at DESC) t
                 JOIN calls c ON c.id = t.call_id
                WHERE c.scores IS NOT NULL""")
        m = _verdict_metrics(cur.fetchall())
        for k in ("agreement", "alarm_precision", "recall", "correct_reliability", "matrix", "deficiency"):
            out[k] = m[k]
        out["by_criterion"] = sorted([r for r in m["by_criterion"] if r["n"] >= 3], key=lambda x: x["v"])
        # «Где отрабатывать»: критерии, генерирующие ложные тревоги и пропуски.
        out["focus"] = sorted([r for r in m["by_criterion"] if r["false_alarms"] or r["misses"]],
                              key=lambda x: -(x["false_alarms"] + x["misses"]))[:10]
        try:  # сколько правил (разборов) уже накоплено по каждому проблемному критерию
            cur.execute("""SELECT COALESCE(direction_name, '—'),criterion_name,COUNT(*)
                             FROM qa_active_policy_rules GROUP BY 1,2""")
            rules = {(r[0], r[1]): r[2] for r in cur.fetchall()}
            for r in out["focus"]:
                r["rules"] = rules.get((r["direction"], r["name"]), 0)
        except Exception:
            pass
        out["reviewed"] = _reviewed_metrics(cur)
        try:
            out["rag"] = _rag_observability(cur)
        except Exception:
            logging.exception("ai-qa RAG observability unavailable")
            out["rag"] = {"status": "error"}
        cur.close(); conn.close()
    except Exception:
        logging.exception("ai-qa stats failed")
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out
