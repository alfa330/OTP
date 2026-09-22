/* «Моя оценка» в карточке ИИ-оценки — правила вердиктов и формула балла.
 *
 * Зеркало call_qa/human_review.py: набор кнопок у критерия, перевод вердикта
 * ИИ в вердикт человека («как у ИИ»), обязательность комментария и балл
 * журнала (src/call_evaluation/main.jsx, totalScore). Сервер считает то же
 * самое и является истиной; здесь — чтобы человек видел балл и подсказки до
 * отправки, а не после отказа.
 *
 * Модуль чистый (без React), его проверяет node-тест
 * tests/ai_qa_human_review.test.mjs.
 */

export const CORRECT = 'Correct';
export const INCORRECT = 'Incorrect';
export const NOT_APPLICABLE = 'N/A';
export const DEFICIENCY = 'Deficiency';
export const ERROR = 'Error';

/** К этим вердиктам журнал требует комментарий («Комментарий обязателен»). */
export const COMMENT_REQUIRED = new Set([INCORRECT, ERROR]);
export const NEGATIVE = new Set([INCORRECT, ERROR, DEFICIENCY]);

export const hasDeficiency = (criterion) => (
    criterion?.deficiency != null && typeof criterion.deficiency === 'object'
    && criterion.deficiency.weight != null
);

/** Кнопки у критерия — ровно те, что в журнале. */
export const allowedVerdicts = (criterion) => {
    if (criterion?.is_critical) return [CORRECT, NOT_APPLICABLE, ERROR];
    return hasDeficiency(criterion)
        ? [CORRECT, INCORRECT, DEFICIENCY, NOT_APPLICABLE]
        : [CORRECT, INCORRECT, NOT_APPLICABLE];
};

/* Вердикт ИИ → вердикт человека. У ИИ нет «Критич. ошибки»: «Неверно» по
 * критическому критерию у него и значит критическое нарушение. «Ожидает»
 * (Pending) — не вердикт, копировать нечего. */
export const verdictFromAi = (criterion, aiVerdict) => {
    if (aiVerdict == null || aiVerdict === '' || aiVerdict === 'Pending') return null;
    let verdict = String(aiVerdict);
    const allowed = allowedVerdicts(criterion);
    if (verdict === INCORRECT && criterion?.is_critical) verdict = ERROR;
    if (verdict === DEFICIENCY && !allowed.includes(DEFICIENCY)) verdict = INCORRECT;
    return allowed.includes(verdict) ? verdict : null;
};

export const isComplete = (criteria, scores) => (
    (criteria || []).length > 0
    && (criteria || []).every((_, i) => scores?.[i] != null && scores[i] !== '')
);

/** Сколько критериев проставлено — для подписи «Заполнено 5 из 12». */
export const filledCount = (criteria, scores) => (
    (criteria || []).reduce((n, _, i) => n + (scores?.[i] != null && scores[i] !== '' ? 1 : 0), 0)
);

/** Балл журнала. null, пока хоть один критерий пуст: частичная сумма читалась бы как низкая оценка. */
export const scoreOf = (criteria, scores) => {
    if (!isComplete(criteria, scores)) return null;
    if (criteria.some((c, i) => c.is_critical && scores[i] === ERROR)) return 0;
    const total = criteria.reduce((sum, c, i) => {
        if (c.is_critical) return sum;
        const v = scores[i];
        if (v === CORRECT || v === NOT_APPLICABLE) return sum + (Number(c.weight) || 0);
        if (v === DEFICIENCY && hasDeficiency(c)) return sum + (Number(c.deficiency.weight) || 0);
        return sum;
    }, 0);
    return Math.round(total);
};

/**
 * Что мешает сохранить. Комментарий к ошибке обязателен всегда; полнота — только
 * если оценка уходит в журнал (там неполной оценки не бывает).
 */
export const validateReview = (criteria, scores, comments, { completeRequired = false } = {}) => {
    const missing = [];
    const commentsRequired = [];
    (criteria || []).forEach((c, i) => {
        const v = scores?.[i];
        if (v == null || v === '') {
            if (completeRequired) missing.push(i);
            return;
        }
        if (COMMENT_REQUIRED.has(v) && !String(comments?.[i] || '').trim()) commentsRequired.push(i);
    });
    return { missing, commentsRequired, ok: missing.length === 0 && commentsRequired.length === 0 };
};

/** Пустая оценка — нечего сохранять. */
export const isEmptyReview = (scores, comments, comment) => (
    !(scores || []).some((v) => v != null && v !== '')
    && !(comments || []).some((t) => String(t || '').trim())
    && !String(comment || '').trim()
);

/** Одна подпись под кнопкой на всё сразу. */
export const validationLabel = ({ missing, commentsRequired }) => {
    const parts = [];
    if (missing?.length) parts.push(`не проставлено: ${missing.length}`);
    if (commentsRequired?.length) parts.push(`нужен комментарий к ошибкам: ${commentsRequired.length}`);
    return parts.join(' · ');
};

/* Подписи вердиктов человека — те же слова, что в журнале. */
export const HUMAN_VERDICT_LABEL = {
    [CORRECT]: 'Корректно',
    [INCORRECT]: 'Ошибка',
    [NOT_APPLICABLE]: 'N/A',
    [DEFICIENCY]: 'Недочёт',
    [ERROR]: 'Критич. ошибка',
};

/** Начальные вердикты из сохранённой оценки, выровненные по шкале карточки. */
export const alignScores = (criteria, saved) => (
    (criteria || []).map((_, i) => {
        const v = saved?.[i];
        return v == null || v === '' ? null : String(v);
    })
);

export const alignComments = (criteria, saved) => (
    (criteria || []).map((_, i) => String(saved?.[i] ?? ''))
);
