"""Слой оценки.

Claude вызывается СЫРЫМ HTTP через httpx (как Gemini в проекте) — без anthropic SDK,
чтобы не тянуть pydantic v2 (конфликтует с aiogram 2.x / pydantic v1). Структурный вывод —
через output_config.format (json_schema). ИИ оценивает только критерии source=transcript;
system_api проверяются провайдером данных (пока нет → Pending), manual → Pending.
Возвращает обычный dict (без pydantic): {"per_criterion": [...], "overall_comment": "..."}."""
from __future__ import annotations
import os
import json

from .. import config
from .. import llm
from . import criterion_config as cc
from .data_checks import get_data_checker
from ..rag import store

_PROMPT = os.path.join(os.path.dirname(__file__), os.pardir, "prompts", "evaluator_system.md")

# JSON-схема ответа Claude (только transcript-критерии).
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "per_criterion": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["Correct", "Incorrect", "N/A"]},
                    "confidence": {"type": "number"},
                    "evidence_quote": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["idx", "verdict", "confidence", "evidence_quote", "comment"],
                "additionalProperties": False,
            },
        },
        "overall_comment": {"type": "string"},
    },
    "required": ["per_criterion", "overall_comment"],
    "additionalProperties": False,
}


def _criteria_block(criteria: list[dict]) -> str:
    out = []
    for c in criteria:
        crit = " (КРИТИЧЕСКИЙ)" if c.get("is_critical") else ""
        out.append(f"{c['idx']}. {c['name']}{crit}\n   Требование: {c['description']}")
    return "\n".join(out)


def build_system(criteria: list[dict]) -> str:
    return open(_PROMPT, encoding="utf-8").read().replace("{{CRITERIA}}", _criteria_block(criteria))


def _render_rag_context(criteria: list[dict], hits_by_idx: dict, status: str) -> tuple[str, dict]:
    """Render one frozen retrieval result globally and per criterion."""
    fallback = ("(retrieval недоступен; не применяй никакие правила из памяти)"
                if status == "degraded" else
                "(подходящих правил выше порога релевантности нет)")
    all_chunks, by_criterion = [], {}
    for criterion in criteria:
        chunks = []
        for hit in hits_by_idx.get(criterion["idx"]) or []:
            chunk = (f"[критерий {criterion['idx']}; правило {hit.get('rule_id')}; "
                     f"сходство {float(hit.get('similarity') or 0):.3f}]\n"
                     f"ситуация применения: {hit.get('situation') or hit.get('excerpt') or '—'}")
            if hit.get("situation") and hit.get("excerpt"):
                chunk += ("\n  исторический фрагмент источника (НЕ является доказательством "
                          f"в текущем звонке): «{hit['excerpt']}»")
            chunk += f"\n  правильно: {hit['correct_verdict']} — потому что: {hit['reason']}"
            if hit.get("not_covered"):
                chunk += f"\n  правило НЕ оправдывает: {hit['not_covered']}"
            chunks.append(chunk)
        by_criterion[str(criterion["idx"])] = "\n\n".join(chunks) if chunks else fallback
        all_chunks.extend(chunks)
    return ("\n\n".join(all_chunks) if all_chunks else fallback), by_criterion


def _prepare_rag(direction_id: int, criteria: list[dict], transcript: str,
                 *, knowledge_snapshot_id=None) -> tuple[str, dict, dict]:
    """Retrieve once for the entire evaluation and build a safe prompt block."""
    query_batch = store.embed_query_chunks(transcript, return_batch=True)
    retrieved = store.retrieve_for_criteria_batch(
        direction_id=direction_id, criteria=criteria, query_batch=query_batch,
        knowledge_snapshot_id=knowledge_snapshot_id, query_text=transcript,
    )
    hits_by_idx = retrieved["hits_by_criterion"]
    used_ids = []
    for c in criteria:
        for h in hits_by_idx.get(c["idx"]) or []:
            used_ids.append(h["id"])
    # Compatibility metric for unmigrated numeric rows.  Canonical exposure
    # counters are derived from qa_retrieval_hits after the run is persisted.
    store.bump_use_count(used_ids)
    block, _ = _render_rag_context(criteria, hits_by_idx, retrieved["status"])
    return block, retrieved["trace"], hits_by_idx


def _rag_block(direction_id: int, criteria: list[dict], transcript: str) -> str:
    """Compatibility wrapper used by older callers/tests."""
    return _prepare_rag(direction_id, criteria, transcript)[0]


def prepare_rag_context(direction: dict, criteria: list[dict], transcript: str, *,
                        use_rag: bool = True, knowledge_snapshot_id=None) -> dict:
    """Freeze the retrieval result so online and Batch retries use one snapshot.

    The returned object is JSON-serialisable and can therefore be stored in a
    durable Batch manifest.  Historical hits are represented by their criterion
    ids only; the prompt text and normalized trace already contain the complete
    auditable retrieval result.
    """
    if use_rag and criteria:
        rag_text, trace, hits_by_idx = _prepare_rag(
            direction["id"], criteria, transcript,
            knowledge_snapshot_id=knowledge_snapshot_id,
        )
        matched = sorted(int(idx) for idx, hits in hits_by_idx.items() if hits)
        _, rag_text_by_criterion = _render_rag_context(
            criteria, hits_by_idx, trace.get("status") or "ok")
    else:
        rag_text = "(RAG отключён для этого варианта оценки)"
        trace = {
            "status": "disabled", "config": {"enabled": False}, "embedding": None,
            "query": {"chunks": [], "embedding_requests": 0, "sql_queries": 0},
            "criteria": [{"criterion_id": c.get("criterion_id"),
                          "criterion_idx": c["idx"], "retrieved_count": 0,
                          "included_count": 0} for c in criteria],
            "candidates": [], "errors": [], "latency_ms": 0,
        }
        matched = []
        rag_text_by_criterion = {
            str(c["idx"]): "(RAG отключён для этого варианта оценки)" for c in criteria
        }
    return {"rag_text": rag_text, "retrieval_trace": trace,
            "matched_criterion_idxs": matched,
            "rag_text_by_criterion": rag_text_by_criterion}


def _subset_rag_text(prepared_rag: dict | None, criteria: list[dict], fallback: str) -> str:
    """Reuse only policy chunks belonging to the criteria in a retry/HARD call."""
    by_criterion = (prepared_rag or {}).get("rag_text_by_criterion")
    if not isinstance(by_criterion, dict):
        return fallback  # compatibility with already persisted Batch manifests
    parts = [by_criterion.get(str(c["idx"])) for c in criteria]
    return "\n\n".join(part for part in parts if part) or fallback


def build_eval_body(transcript, direction, criteria, *, asr_low_spans=None, use_rag=True, model,
                    rag_text=None, knowledge_snapshot_id=None, retrieval_trace_out=None) -> dict:
    """Тело запроса оценки (для синхронного вызова и для Batch API)."""
    if use_rag and rag_text is None:
        rag, trace, _ = _prepare_rag(direction["id"], criteria, transcript,
                                     knowledge_snapshot_id=knowledge_snapshot_id)
        if retrieval_trace_out is not None:
            retrieval_trace_out.update(trace)
    else:
        rag = rag_text if use_rag else "(RAG отключён для этого варианта оценки)"
    low = ("\nНЕУВЕРЕННЫЕ ФРАГМЕНТЫ РАСПОЗНАВАНИЯ (не штрафовать):\n"
           + json.dumps(asr_low_spans, ensure_ascii=False)) if asr_low_spans else ""
    user = (f"РАЗБОРЫ (согласованные прецеденты):\n{rag}\n\n"
            f"ТРАНСКРИПТ ЗВОНКА:\n{transcript}{low}\n\nОцени по всем перечисленным критериям.")
    return llm.build_body(model=model, system=build_system(criteria), user=user,
                          schema=_OUTPUT_SCHEMA, max_tokens=8000, cache_system=True)


def _claude_eval(transcript, direction, criteria, *, asr_low_spans, use_rag, model,
                 rag_text=None, stage="primary") -> dict:
    """Оценка подмножества (transcript) критериев моделью `model`."""
    body = build_eval_body(transcript, direction, criteria, asr_low_spans=asr_low_spans,
                           use_rag=use_rag, model=model, rag_text=rag_text)
    result = llm.post_body(body, timeout=120.0, include_meta=True)
    if result.get("_llm_meta") is not None:
        result["_llm_meta"]["stage"] = stage
        result["_llm_meta"]["criterion_idxs"] = [c["idx"] for c in criteria]
    return result


def _needs_escalation(v: dict, crit: dict) -> bool:
    """На HARD-модель уходит: ЛЮБОЙ вердикт Incorrect (высокая цена ошибки — не штрафуем
    оператора без второго мнения сильной модели; там же применяются разборы), ИЛИ низкая
    уверенность модели (порог включительно: ровно 0.6 — тоже сомнение)."""
    if v.get("verdict") == "Incorrect":
        return True
    conf = v.get("confidence")
    if conf is not None and conf <= config.ESCALATE_CONF:
        return True
    return False


def _collect_verdicts(items) -> dict:
    """per_criterion → {idx: verdict}. Дубли одного idx: одинаковый вердикт — берём с большей
    уверенностью; ПРОТИВОРЕЧАЩИЕ вердикты — не доверяем ни одному (критерий пойдёт на повтор).
    Сбой формата не должен превращаться в зачёт или штраф."""
    by, conflicted = {}, set()
    for v in items or []:
        idx = v.get("idx")
        if idx is None or idx in conflicted:
            continue
        cur = by.get(idx)
        if cur is None:
            by[idx] = v
        elif cur.get("verdict") == v.get("verdict"):
            if (v.get("confidence") or 0) > (cur.get("confidence") or 0):
                by[idx] = v
        else:
            conflicted.add(idx)
            by.pop(idx, None)
    return by


def evaluate(transcript: str, direction: dict, *, asr_low_spans=None, use_rag=True,
             call_context=None, knowledge_snapshot_id=None, prepared_rag=None,
             primary_result=None, primary_llm_meta=None) -> dict:
    """Полная оценка. Двухуровнево: массовая модель (BULK) первым проходом, затем спорные/
    критические критерии переоцениваются HARD-моделью. Плюс маршрутизация по источнику."""
    cc.apply_to_direction(direction)
    crit_by_idx = {c["idx"]: c for c in direction["criteria"]}
    t_crits = [c for c in direction["criteria"] if c["eval_source"] == cc.TRANSCRIPT]

    if prepared_rag is not None:
        rag_text = prepared_rag["rag_text"]
        retrieval_trace = prepared_rag.get("retrieval_trace") or {}
        hits_by_idx = {int(idx): [{}]
                       for idx in prepared_rag.get("matched_criterion_idxs") or []}
    elif use_rag and t_crits:
        prepared_rag = prepare_rag_context(
            direction, t_crits, transcript, use_rag=True,
            knowledge_snapshot_id=knowledge_snapshot_id,
        )
        rag_text = prepared_rag["rag_text"]
        retrieval_trace = prepared_rag["retrieval_trace"]
        hits_by_idx = {int(idx): [{}]
                       for idx in prepared_rag.get("matched_criterion_idxs") or []}
    else:
        rag_text, hits_by_idx = "(RAG отключён для этого варианта оценки)", {}
        retrieval_trace = {
            "status": "disabled", "config": {"enabled": False}, "embedding": None,
            "query": {"chunks": [], "embedding_requests": 0, "sql_queries": 0},
            "criteria": [{"criterion_id": c.get("criterion_id"), "criterion_idx": c["idx"],
                          "retrieved_count": 0, "included_count": 0} for c in t_crits],
            "candidates": [], "errors": [], "latency_ms": 0,
        }
    llm_calls = []

    # 1) первый проход
    ai = (primary_result if primary_result is not None else
          (_claude_eval(transcript, direction, t_crits, asr_low_spans=asr_low_spans,
                        use_rag=use_rag, model=config.CLAUDE_MODEL_BULK,
                        rag_text=rag_text, stage="bulk")
           if t_crits else {"per_criterion": [], "overall_comment": ""}))
    if primary_llm_meta and not ai.get("_llm_meta"):
        llm_calls.append(primary_llm_meta)
    if ai.get("_llm_meta"):
        llm_calls.append(ai["_llm_meta"])
    by_idx = _collect_verdicts(ai.get("per_criterion"))
    model_by_idx = {idx: config.CLAUDE_MODEL_BULK for idx in by_idx}

    # 1.1) обрыв/дубли ответа: невозвращённые критерии повторяем один раз той же моделью —
    #      иначе сбой формата молча оставил бы критерии без оценки.
    missing = [c for c in t_crits if c["idx"] not in by_idx]
    if missing:
        retry_rag_text = _subset_rag_text(prepared_rag, missing, rag_text)
        retry = _claude_eval(transcript, direction, missing, asr_low_spans=asr_low_spans,
                             use_rag=use_rag, model=config.CLAUDE_MODEL_BULK,
                             rag_text=retry_rag_text, stage="bulk_retry")
        if retry.get("_llm_meta"):
            llm_calls.append(retry["_llm_meta"])
        for idx, v in _collect_verdicts(retry.get("per_criterion")).items():
            if idx not in by_idx:
                by_idx[idx] = v
                model_by_idx[idx] = config.CLAUDE_MODEL_BULK

    # 2) эскалация на HARD-модель (только если она отличается от BULK): спорные /
    #    не вернувшиеся вердикты + КРИТЕРИИ С РАЗБОРОМ — по ним решение принимает
    #    сильная модель, даже если BULK уверенно поставил Correct.
    two_tier = bool(config.CLAUDE_MODEL_HARD) and config.CLAUDE_MODEL_HARD != config.CLAUDE_MODEL_BULK
    if two_tier:
        adj_criteria = {idx for idx, criterion_hits in hits_by_idx.items() if criterion_hits}
        escalate = [c for c in t_crits
                    if c["idx"] not in by_idx
                    or _needs_escalation(by_idx[c["idx"]], crit_by_idx[c["idx"]])
                    or c["idx"] in adj_criteria]
        if escalate:
            hard_rag_text = _subset_rag_text(prepared_rag, escalate, rag_text)
            ai2 = _claude_eval(transcript, direction, escalate, asr_low_spans=asr_low_spans,
                               use_rag=use_rag, model=config.CLAUDE_MODEL_HARD,
                               rag_text=hard_rag_text, stage="hard")
            if ai2.get("_llm_meta"):
                llm_calls.append(ai2["_llm_meta"])
            for idx, v in _collect_verdicts(ai2.get("per_criterion")).items():
                by_idx[idx] = v
                model_by_idx[idx] = config.CLAUDE_MODEL_HARD

    result = assemble_results(direction, by_idx, model_by_idx,
                              call_context=call_context, overall_comment=ai.get("overall_comment", ""))
    result["retrieval_trace"] = retrieval_trace
    result["_llm_meta"] = {
        "calls": llm_calls,
        "total_calls": len(llm_calls),
        "total_latency_ms": sum(int(call.get("latency_ms") or 0) for call in llm_calls),
    }
    return result


def assemble_results(direction: dict, by_idx: dict, model_by_idx: dict, *,
                     call_context=None, overall_comment="") -> dict:
    """Вердикты модели + маршрутизация по источнику критерия → итоговая структура оценки.
    Используется и синхронным evaluate(), и пакетной оценкой (batch_eval)."""
    checker = get_data_checker()
    results = []
    for c in direction["criteria"]:
        base = {"idx": c["idx"], "criterion_id": c.get("criterion_id"),
                "name": c["name"], "source": c["eval_source"]}
        src = c["eval_source"]
        if src == cc.TRANSCRIPT:
            v = by_idx.get(c["idx"])
            if v:
                results.append({**base, "verdict": v.get("verdict", "N/A"), "confidence": v.get("confidence"),
                                "evidence_quote": v.get("evidence_quote", ""), "comment": v.get("comment", ""),
                                "model": model_by_idx.get(c["idx"])})
            else:
                results.append({**base, "verdict": "Pending", "confidence": None,
                                "evidence_quote": "", "comment": "ИИ не вернул вердикт", "model": None})
        elif src == cc.SYSTEM_API:
            if checker.supports(c) and call_context:
                rr = checker.check(c, call_context)
                results.append({**base, "verdict": rr.get("verdict", "Pending"), "confidence": rr.get("confidence"),
                                "evidence_quote": rr.get("evidence", ""), "comment": rr.get("comment", "проверка данных в ПО"), "model": None})
            elif c.get("default_verdict"):
                results.append({**base, "verdict": c["default_verdict"], "confidence": None,
                                "evidence_quote": "", "comment": "по умолчанию (нет API проверки данных)", "model": None})
            else:
                results.append({**base, "verdict": "Pending", "confidence": None,
                                "evidence_quote": "", "comment": "нужна проверка данных в ПО (API пока нет)", "model": None})
        else:
            results.append({**base, "verdict": "Pending", "confidence": None,
                            "evidence_quote": "", "comment": "ручная проверка", "model": None})

    return {"per_criterion": results, "overall_comment": overall_comment}
