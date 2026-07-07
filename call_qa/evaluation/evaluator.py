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


def _rag_block(direction_id: int, criteria: list[dict], transcript: str) -> str:
    # Оптимизация: embedding текущего транскрипта считаем один раз на всю оценку,
    # а не отдельно на каждый критерий. Retrieval остаётся семантическим: внутри
    # каждого критерия pgvector выбирает самые похожие сохранённые разборы.
    query_vector = store.embed_query(transcript) if transcript else None
    chunks = []
    for c in criteria:
        try:
            hits = store.retrieve(direction_id=direction_id, criterion_idx=c["idx"], query_vector=query_vector)
        except Exception:
            hits = []
        for h in hits:
            chunk = f"[критерий {c['idx']}] ситуация: {h.get('situation') or h['excerpt']}"
            if h.get("situation") and h.get("excerpt"):
                chunk += f"\n  цитата из звонка-источника: «{h['excerpt']}»"
            chunk += f"\n  правильно: {h['correct_verdict']} — потому что: {h['reason']}"
            if h.get("not_covered"):
                chunk += f"\n  правило НЕ оправдывает: {h['not_covered']}"
            chunks.append(chunk)
    return "\n".join(chunks) if chunks else "(прецедентов пока нет)"


def _claude_eval(transcript, direction, criteria, *, asr_low_spans, use_rag, model) -> dict:
    """Оценка подмножества (transcript) критериев моделью `model`."""
    rag = _rag_block(direction["id"], criteria, transcript) if use_rag else "(RAG отключён)"
    low = ("\nНЕУВЕРЕННЫЕ ФРАГМЕНТЫ РАСПОЗНАВАНИЯ (не штрафовать):\n"
           + json.dumps(asr_low_spans, ensure_ascii=False)) if asr_low_spans else ""
    user = (f"РАЗБОРЫ (согласованные прецеденты):\n{rag}\n\n"
            f"ТРАНСКРИПТ ЗВОНКА:\n{transcript}{low}\n\nОцени по всем перечисленным критериям.")
    return llm.claude_json(model=model, system=build_system(criteria), user=user,
                           schema=_OUTPUT_SCHEMA, max_tokens=8000, timeout=120.0,
                           cache_system=True)  # {"per_criterion": [...], "overall_comment": "..."}


def _needs_escalation(v: dict, crit: dict) -> bool:
    """На HARD-модель уходит: ЛЮБОЙ вердикт Incorrect (высокая цена ошибки — не штрафуем
    оператора без второго мнения сильной модели; там же применяются разборы), ИЛИ низкая
    уверенность модели."""
    if v.get("verdict") == "Incorrect":
        return True
    conf = v.get("confidence")
    if conf is not None and conf < config.ESCALATE_CONF:
        return True
    return False


def evaluate(transcript: str, direction: dict, *, asr_low_spans=None, use_rag=True, call_context=None) -> dict:
    """Полная оценка. Двухуровнево: массовая модель (BULK) первым проходом, затем спорные/
    критические критерии переоцениваются HARD-моделью. Плюс маршрутизация по источнику."""
    cc.apply_to_direction(direction)
    crit_by_idx = {c["idx"]: c for c in direction["criteria"]}
    t_crits = [c for c in direction["criteria"] if c["eval_source"] == cc.TRANSCRIPT]

    # 1) первый проход — массовая (дешёвая) модель
    ai = (_claude_eval(transcript, direction, t_crits, asr_low_spans=asr_low_spans,
                       use_rag=use_rag, model=config.CLAUDE_MODEL_BULK)
          if t_crits else {"per_criterion": [], "overall_comment": ""})
    by_idx = {v["idx"]: v for v in ai.get("per_criterion", [])}
    model_by_idx = {idx: config.CLAUDE_MODEL_BULK for idx in by_idx}

    # 2) эскалация на HARD-модель: спорные / не вернувшиеся вердикты + КРИТЕРИИ С РАЗБОРОМ.
    #    Последнее закрывает дыру: если по критерию есть разбор, решение принимает сильная
    #    модель, даже если BULK уверенно поставил Correct (иначе такой случай был бы упущен).
    adj_criteria = store.criteria_with_adjudications(direction["id"]) if use_rag else set()
    escalate = [c for c in t_crits
                if c["idx"] not in by_idx
                or _needs_escalation(by_idx[c["idx"]], crit_by_idx[c["idx"]])
                or c["idx"] in adj_criteria]
    if escalate and config.CLAUDE_MODEL_HARD and config.CLAUDE_MODEL_HARD != config.CLAUDE_MODEL_BULK:
        ai2 = _claude_eval(transcript, direction, escalate, asr_low_spans=asr_low_spans,
                           use_rag=use_rag, model=config.CLAUDE_MODEL_HARD)
        for v in ai2.get("per_criterion", []):
            by_idx[v["idx"]] = v
            model_by_idx[v["idx"]] = config.CLAUDE_MODEL_HARD

    checker = get_data_checker()
    results = []
    for c in direction["criteria"]:
        base = {"idx": c["idx"], "name": c["name"], "source": c["eval_source"]}
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

    return {"per_criterion": results, "overall_comment": ai.get("overall_comment", "")}
