"""«Моя оценка» проверяющего в карточке ИИ-оценки.

Человек оценивает субъект по той же мониторинговой шкале, что и ИИ, прямо в
карточке ревью — не переходя в «Журнал оценок». Запись живёт в
``ai_human_reviews`` и служит двум целям:

* калибровка: пер-критерийные вердикты человека сравниваются с вердиктами ИИ
  в метриках согласия (dashboard) — для этого оценка может быть НЕПОЛНОЙ;
* качество сотрудника: по желанию проверяющего («учитывать в качестве») та же
  оценка уходит в журнал обычной строкой ``calls`` — и тогда обязана быть
  полной, как в самом журнале: каждый критерий проставлен, у ошибок есть
  комментарий.

Формула балла и набор вердиктов повторяют журнал (src/call_evaluation/main.jsx,
``CriterionCard`` + ``totalScore``): критический критерий знает только
«Корректно / N/A / Критич. ошибка», остальные — «Корректно / Ошибка / N/A» и
«Недочёт», если шкала его предусматривает. Зеркало на фронте —
src/components/call_qa/humanReview.js; расхождение между ними означало бы, что
балл в карточке и балл в журнале считаются по-разному.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from psycopg2.extras import Json

from . import config

ALMATY = ZoneInfo("Asia/Almaty")

CORRECT = "Correct"
INCORRECT = "Incorrect"
NOT_APPLICABLE = "N/A"
DEFICIENCY = "Deficiency"
ERROR = "Error"          # критическая ошибка — только у критического критерия

HUMAN_VERDICTS = (CORRECT, INCORRECT, NOT_APPLICABLE, DEFICIENCY, ERROR)
# Вердикты, к которым журнал требует комментарий («Комментарий обязателен»).
COMMENT_REQUIRED = frozenset({INCORRECT, ERROR})
NEGATIVE = frozenset({INCORRECT, ERROR, DEFICIENCY})

_ALIASES = {
    "correct": CORRECT, "ok": CORRECT, "верно": CORRECT, "корректно": CORRECT,
    "incorrect": INCORRECT, "неверно": INCORRECT, "ошибка": INCORRECT,
    "n/a": NOT_APPLICABLE, "na": NOT_APPLICABLE, "неприменимо": NOT_APPLICABLE,
    "deficiency": DEFICIENCY, "недочёт": DEFICIENCY, "недочет": DEFICIENCY,
    "error": ERROR, "критич. ошибка": ERROR, "critical": ERROR,
}


def normalise_verdict(value):
    """Строка любого регистра → канонический вердикт; пусто → None; чужое → ValueError."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    verdict = _ALIASES.get(text.lower())
    if verdict is None:
        raise ValueError(f"неизвестный вердикт: {text!r}")
    return verdict


def has_deficiency(criterion: dict) -> bool:
    deficiency = criterion.get("deficiency")
    return isinstance(deficiency, dict) and deficiency.get("weight") is not None


def allowed_verdicts(criterion: dict) -> tuple[str, ...]:
    """Что можно поставить по критерию — ровно кнопки журнала."""
    if criterion.get("is_critical"):
        return (CORRECT, NOT_APPLICABLE, ERROR)
    if has_deficiency(criterion):
        return (CORRECT, INCORRECT, DEFICIENCY, NOT_APPLICABLE)
    return (CORRECT, INCORRECT, NOT_APPLICABLE)


def verdict_from_ai(criterion: dict, ai_verdict) -> str | None:
    """Вердикт ИИ → вердикт человека для кнопки «как у ИИ».

    Набор у ИИ другой: он не ставит «Критич. ошибку», а «Неверно» по критическому
    критерию у него и означает критическое нарушение (так же считает и _ai_score:
    критический Incorrect → 0). «Ожидает» (Pending) — не вердикт: копировать
    нечего, критерий остаётся пустым."""
    try:
        verdict = normalise_verdict(ai_verdict)
    except ValueError:
        return None
    if verdict is None:
        return None
    allowed = allowed_verdicts(criterion)
    if verdict == INCORRECT and criterion.get("is_critical"):
        verdict = ERROR
    if verdict == DEFICIENCY and DEFICIENCY not in allowed:
        verdict = INCORRECT
    return verdict if verdict in allowed else None


def normalise_scores(criteria: list[dict], raw) -> list:
    """Список вердиктов 1:1 со шкалой (None — критерий не проставлен).

    Длина обязана совпадать со шкалой: короче — карточка устарела относительно
    шкалы, длиннее — прислали лишнее; и то и другое молча смещало бы баллы."""
    raw = list(raw or [])
    if len(raw) > len(criteria):
        raise ValueError("оценок больше, чем критериев в шкале — переоткройте карточку")
    raw += [None] * (len(criteria) - len(raw))
    scores = []
    for criterion, value in zip(criteria, raw):
        verdict = normalise_verdict(value)
        if verdict is not None and verdict not in allowed_verdicts(criterion):
            raise ValueError(
                f"вердикт «{verdict}» недопустим для критерия «{criterion.get('name')}»")
        scores.append(verdict)
    return scores


def normalise_comments(criteria: list[dict], raw) -> list[str]:
    raw = list(raw or [])
    if len(raw) > len(criteria):
        raise ValueError("комментариев больше, чем критериев в шкале")
    raw += [""] * (len(criteria) - len(raw))
    return [str(value or "").strip() for value in raw]


def is_complete(scores: list) -> bool:
    return bool(scores) and all(value is not None for value in scores)


def is_empty(scores: list, comments: list, comment: str | None = None) -> bool:
    """Нечего сохранять: ни одного вердикта и ни одного слова."""
    return (not any(value is not None for value in scores)
            and not any(text for text in comments)
            and not str(comment or "").strip())


def validate(criteria: list[dict], scores: list, comments: list, *,
             complete_required: bool) -> dict:
    """Что мешает сохранить: непроставленные критерии и ошибки без комментария.

    Комментарий к ошибке обязателен ВСЕГДА — и у калибровочной оценки: без него
    расхождение с ИИ нечем объяснить. Полнота требуется только оценке, которая
    уходит в журнал: там неполной оценки не бывает."""
    missing, comments_required = [], []
    for criterion, verdict, text in zip(criteria, scores, comments):
        idx = criterion.get("idx")
        if verdict is None:
            if complete_required:
                missing.append(idx)
            continue
        if verdict in COMMENT_REQUIRED and not text:
            comments_required.append(idx)
    return {"missing": missing, "comments_required": comments_required}


def validation_message(problems: dict) -> str | None:
    parts = []
    if problems.get("missing"):
        parts.append(f"не проставлено критериев: {len(problems['missing'])}")
    if problems.get("comments_required"):
        parts.append(f"нужен комментарий к ошибкам: {len(problems['comments_required'])}")
    return "; ".join(parts) or None


def score_of(criteria: list[dict], scores: list):
    """Балл журнала (main.jsx totalScore). None, пока хоть один критерий пуст:
    частичная сумма читалась бы как низкая оценка."""
    if not is_complete(scores):
        return None
    verdict_by_idx = dict(zip((c.get("idx") for c in criteria), scores))
    for criterion in criteria:
        if criterion.get("is_critical") and verdict_by_idx.get(criterion.get("idx")) == ERROR:
            return 0
    total = 0.0
    for criterion in criteria:
        if criterion.get("is_critical"):
            continue
        verdict = verdict_by_idx.get(criterion.get("idx"))
        if verdict in (CORRECT, NOT_APPLICABLE):
            total += float(criterion.get("weight") or 0)
        elif verdict == DEFICIENCY and has_deficiency(criterion):
            total += float(criterion["deficiency"].get("weight") or 0)
    return round(total)


# ── хранение ─────────────────────────────────────────────────────────────────

_COLUMNS = ("id", "subject_kind", "call_id", "reviewer_id", "direction_id",
            "evaluation_run_id", "scores", "criterion_comments", "score", "comment",
            "comment_visible_to_operator", "question_resolved", "resolved_first_contact",
            "counted_in_quality", "journal_call_id", "created_at", "updated_at")
_SELECT = "SELECT " + ", ".join(_COLUMNS) + " FROM ai_human_reviews"


def _row(values) -> dict | None:
    if not values:
        return None
    return dict(zip(_COLUMNS, values))


def _local_str(moment) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ALMATY).strftime("%d.%m.%Y %H:%M")


def serialise(row: dict | None, *, source: str = "review") -> dict | None:
    """Форма для карточки. ``source`` — откуда взята оценка: своя строка
    ('review') либо строка журнала, сделанная этим же человеком ('journal')."""
    if not row:
        return None
    scores = row.get("scores") if isinstance(row.get("scores"), list) else []
    comments = (row.get("criterion_comments")
                if isinstance(row.get("criterion_comments"), list) else [])
    return {
        "id": row.get("id"),
        "source": source,
        "scores": scores,
        "criterion_comments": comments,
        "score": row.get("score"),
        "comment": row.get("comment") or "",
        "comment_visible_to_operator": bool(row.get("comment_visible_to_operator", True)),
        "question_resolved": bool(row.get("question_resolved")),
        "resolved_first_contact": row.get("resolved_first_contact"),
        "counted_in_quality": bool(row.get("counted_in_quality")),
        "journal_call_id": row.get("journal_call_id"),
        "complete": is_complete(scores),
        "updated_at": _local_str(row.get("updated_at")),
    }


def get_review(subject_kind: str, call_id: int, reviewer_id: int) -> dict | None:
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(_SELECT + " WHERE subject_kind = %s AND call_id = %s AND reviewer_id = %s",
                    (str(subject_kind), int(call_id), int(reviewer_id)))
        row = _row(cur.fetchone())
        cur.close()
        return row
    finally:
        conn.close()


def upsert_review(*, subject_kind: str, call_id: int, reviewer_id: int, direction_id: int,
                  evaluation_run_id, scores: list, criterion_comments: list, score,
                  comment: str | None, comment_visible_to_operator: bool = True,
                  question_resolved: bool = False, resolved_first_contact=None) -> dict:
    """Одна запись на проверяющего и субъект: повторное сохранение перезаписывает.
    Флаг журнала здесь НЕ трогается — его ставит mark_counted после записи в calls."""
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET client_encoding TO 'UTF8'")
            cur.execute(
                """INSERT INTO ai_human_reviews
                     (subject_kind, call_id, reviewer_id, direction_id, evaluation_run_id,
                      scores, criterion_comments, score, comment, comment_visible_to_operator,
                      question_resolved, resolved_first_contact)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (subject_kind, call_id, reviewer_id) DO UPDATE SET
                     direction_id = EXCLUDED.direction_id,
                     evaluation_run_id = COALESCE(EXCLUDED.evaluation_run_id,
                                                  ai_human_reviews.evaluation_run_id),
                     scores = EXCLUDED.scores,
                     criterion_comments = EXCLUDED.criterion_comments,
                     score = EXCLUDED.score,
                     comment = EXCLUDED.comment,
                     comment_visible_to_operator = EXCLUDED.comment_visible_to_operator,
                     question_resolved = EXCLUDED.question_resolved,
                     resolved_first_contact = EXCLUDED.resolved_first_contact,
                     updated_at = now()
                   RETURNING """ + ", ".join(_COLUMNS),
                (str(subject_kind), int(call_id), int(reviewer_id), int(direction_id),
                 str(evaluation_run_id) if evaluation_run_id else None,
                 Json(list(scores)), Json(list(criterion_comments)),
                 float(score) if score is not None else None,
                 (str(comment or "").strip() or None), bool(comment_visible_to_operator),
                 bool(question_resolved),
                 (bool(resolved_first_contact) if question_resolved else None)))
            row = _row(cur.fetchone())
        return row
    finally:
        conn.close()


def mark_counted(review_id: int, journal_call_id: int) -> None:
    """Оценка ушла в журнал: запоминаем строку calls, чтобы следующее сохранение
    было переоценкой этой строки, а не второй оценкой того же разговора."""
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE ai_human_reviews
                      SET counted_in_quality = TRUE, journal_call_id = %s, updated_at = now()
                    WHERE id = %s""",
                (int(journal_call_id), int(review_id)))
    finally:
        conn.close()


def latest_scored(subject_kind: str, call_id: int) -> dict | None:
    """Последняя ПОЛНАЯ оценка человека по субъекту (любого проверяющего) —
    для списков, где у субъекта нет строки журнала."""
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(_SELECT + """ WHERE subject_kind = %s AND call_id = %s AND score IS NOT NULL
                                  ORDER BY updated_at DESC LIMIT 1""",
                    (str(subject_kind), int(call_id)))
        row = _row(cur.fetchone())
        cur.close()
        return row
    finally:
        conn.close()


def now_local_iso() -> str:  # pragma: no cover — обёртка времени для тестов
    return datetime.now(ALMATY).replace(tzinfo=None).isoformat(timespec="seconds")


__all__ = [
    "HUMAN_VERDICTS", "COMMENT_REQUIRED", "NEGATIVE", "allowed_verdicts", "verdict_from_ai",
    "normalise_verdict", "normalise_scores", "normalise_comments", "is_complete", "is_empty",
    "validate", "validation_message", "score_of", "serialise", "get_review", "upsert_review",
    "mark_counted", "latest_scored",
]

logging.getLogger(__name__).addHandler(logging.NullHandler())
