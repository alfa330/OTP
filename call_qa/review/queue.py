"""Маршрутизация в ревью + хук авто-сохранения разбора в RAG.
on_adjudication() вызывается из существующего экшена ревью — разбор сохраняется сам."""
from __future__ import annotations

from .. import config
from ..rag import store


# Порядок = серьёзность (важнее — раньше): ключи совпадают с бейджами фронтенда (CallQaView.REASON).
REASON_PRIORITY = ("critical", "lowconf", "pending", "asr")


def review_reasons(criteria, asr_mean_conf=None) -> list[str]:
    """Почему звонок требует человека — по критериям В ФОРМЕ КАРТОЧКИ (payload['criteria']:
    {idx, is_critical, source, ai, conf}). Пустой список = флагов нет, ревью не обязательно.
    Те же правила, что needs_review(), но на сохранённой карточке — для очереди ревью."""
    reasons = set()
    if asr_mean_conf is not None and asr_mean_conf < config.ASR_CONF_HARD:
        reasons.add("asr")
    for cr in criteria or []:
        v = cr.get("ai")
        if v == "Pending":
            reasons.add("pending")
        if cr.get("source") == "transcript":
            conf = cr.get("conf")
            if conf is not None and conf <= config.REVIEW_MODEL_CONF:
                reasons.add("lowconf")
            if cr.get("is_critical") and v == "Incorrect":
                reasons.add("critical")
    return [r for r in REASON_PRIORITY if r in reasons]


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
