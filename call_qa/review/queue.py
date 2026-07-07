"""Маршрутизация в ревью + хук авто-сохранения разбора в RAG.
on_adjudication() вызывается из существующего экшена ревью — разбор сохраняется сам."""
from __future__ import annotations

from .. import config
from ..rag import store


def needs_review(result, direction: dict, asr_mean_conf: float | None) -> bool:
    """В ревью уходит звонок, если: плохое распознавание, есть Pending (нужна проверка
    данных/человек), низкая уверенность ИИ, или спорный критический критерий."""
    if asr_mean_conf is not None and asr_mean_conf < config.ASR_CONF_HARD:
        return True
    crit_idx = {c["idx"] for c in direction["criteria"] if c.get("is_critical")}
    for v in result["per_criterion"]:
        if v["verdict"] == "Pending":
            return True   # системный (нет API) или ручной критерий
        if v["source"] == "transcript" and v["confidence"] is not None and v["confidence"] <= config.REVIEW_MODEL_CONF:
            return True  # порог включительно: ровно 0.6 — тоже сомнение
        if v["idx"] in crit_idx and v["verdict"] == "Incorrect":
            return True   # критический «Incorrect» — всегда подтверждает человек
    return False


def on_adjudication(*, direction_id, criterion_idx, criterion_name, call_id,
                    excerpt, ai_verdict, correct_verdict, reason,
                    not_covered=None, situation=None, situation_tag=None, reviewer_id=None) -> int:
    """Человек разобрал спорный критерий → кладём в RAG (с embedding). Возвращает id записи."""
    return store.save_adjudication(
        direction_id=direction_id, criterion_idx=criterion_idx, criterion_name=criterion_name,
        call_id=call_id, excerpt=excerpt, ai_verdict=ai_verdict, correct_verdict=correct_verdict,
        reason=reason, not_covered=not_covered, situation=situation, situation_tag=situation_tag,
        created_by=reviewer_id)
