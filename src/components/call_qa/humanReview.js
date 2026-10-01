/* Оценка по критериям в карточке ИИ-оценки — правила вердиктов и формула балла.
 *
 * Зеркало call_qa/human_review.py: набор кнопок у критерия, перевод вердикта
 * ИИ в вердикт человека (им карточка и заполнена с самого начала), что считать
 * исправлением ИИ, обязательность комментария и балл журнала
 * (src/call_evaluation/journalScore.js). Сервер считает то же самое и
 * является истиной; здесь — чтобы человек видел балл и подсказки до отправки, а
 * не после отказа.
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

/* Сколько баллов снимает с итога «Недочёт» по критическому критерию. Поле в
 * шкале одно — deficiency.weight, смысл задаёт критичность: у взвешенного
 * критерия это частичный зачёт, у критического — вычет вместо обнуления. */
export const criticalDeficiencyPenalty = (criterion) => (
    criterion?.is_critical && hasDeficiency(criterion)
        ? Math.max(0, Number(criterion.deficiency.weight) || 0)
        : 0
);

/** Кнопки у критерия — ровно те, что в журнале. */
export const allowedVerdicts = (criterion) => {
    if (criterion?.is_critical) {
        return hasDeficiency(criterion)
            ? [CORRECT, NOT_APPLICABLE, DEFICIENCY, ERROR]
            : [CORRECT, NOT_APPLICABLE, ERROR];
    }
    return hasDeficiency(criterion)
        ? [CORRECT, INCORRECT, DEFICIENCY, NOT_APPLICABLE]
        : [CORRECT, INCORRECT, NOT_APPLICABLE];
};

/* Вердикт ИИ → вердикт человека. У ИИ нет «Критич. ошибки»: «Неверно» по
 * критическому критерию у него и значит критическое нарушение. «Ожидает»
 * (Pending) — не вердикт, копировать нечего. «Error» бывал в старых прогонах
 * (GLM писал его вне схемы) — по некритическому критерию это обычная ошибка. */
export const verdictFromAi = (criterion, aiVerdict) => {
    if (aiVerdict == null || aiVerdict === '' || aiVerdict === 'Pending') return null;
    let verdict = String(aiVerdict);
    const allowed = allowedVerdicts(criterion);
    if (verdict === ERROR) verdict = INCORRECT;
    if (verdict === INCORRECT && criterion?.is_critical) verdict = ERROR;
    if (verdict === DEFICIENCY && !allowed.includes(DEFICIENCY)) verdict = INCORRECT;
    return allowed.includes(verdict) ? verdict : null;
};

/* Вердикт человека → словарь ИИ (разбор в базу знаний пишется в нём):
 * «Критич. ошибка» у ИИ — это «Неверно» по критическому критерию. */
export const toAiVerdict = (verdict) => (verdict === ERROR ? INCORRECT : verdict ?? null);

const AI_VERDICTS = new Set([CORRECT, INCORRECT, NOT_APPLICABLE, DEFICIENCY]);

/* Вердикт ИИ по критерию, с которым сравнивается решение человека. null — ИИ
 * критерий не оценивал (не по транскрипту или Pending): человек ставит его сам, и
 * это не исправление ИИ. «Error» старых прогонов — то же «Неверно». */
export const aiVerdictOf = (criterion) => {
    if (criterion?.source !== 'transcript') return null;
    const raw = criterion?.ai;
    if (raw == null || raw === '') return null;
    const verdict = String(raw) === ERROR ? INCORRECT : String(raw);
    return AI_VERDICTS.has(verdict) ? verdict : null;
};

/* Исправил ли человек ИИ по этому критерию. Такое расхождение и есть разбор:
 * он уходит в черновики базы знаний и требует объяснения. */
export const isCorrection = (criterion, verdict) => {
    const ai = aiVerdictOf(criterion);
    return ai != null && verdict != null && verdict !== '' && toAiVerdict(verdict) !== ai;
};

/* Комментарий к критерию, который приходит вместе с вердиктом ИИ. Нужен там, где
 * журнал требует объяснения ошибки: человек, согласный с ИИ, ничего не меняет — и
 * комментарий к ошибке уже есть. К положительным вердиктам не подставляем: там он
 * необязателен и читался бы оператором как замечание. */
export const aiCommentFor = (criterion, verdict) => (
    verdict != null && NEGATIVE.has(verdict) && verdictFromAi(criterion, criterion?.ai) === verdict
        ? String(criterion?.comment || '').trim()
        : ''
);

/* Стартовая оценка карточки. Своя сохранённая оценка — как есть; без неё —
 * вердикты ИИ: согласие с ИИ не требует ни одного нажатия, а правка одного
 * критерия — одного нажатия и комментария. Критерии, которые ИИ не оценивал,
 * остаются пустыми — их проверяет человек. */
export const initialVerdicts = (criteria, saved) => {
    if (saved) {
        return {
            scores: alignScores(criteria, saved.scores),
            comments: alignComments(criteria, saved.criterion_comments),
        };
    }
    const scores = (criteria || []).map((c) => verdictFromAi(c, c.ai));
    return { scores, comments: (criteria || []).map((c, i) => aiCommentFor(c, scores[i])) };
};

/* Предпроверка цитаты повторяет нормализацию сервера (call_qa/review/evidence.py:
 * NFKC + приведение регистра, только буквы/цифры, схлопывание пробелов): цитату,
 * которой нет в транскрипте дословно, человек видит сразу, а не отказом при
 * сохранении. Фрагменты короче 4 символов сервер тоже не принимает. */
const normalizeForMatch = (value) => String(value || '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();

export const quoteInTranscript = (excerpt, transcriptText) => {
    const needle = normalizeForMatch(excerpt);
    if (needle.length < 4) return false;
    return normalizeForMatch(transcriptText).includes(needle);
};

/* Что мешает отправить исправления ИИ в базу знаний: у каждого исправления нужно
 * объяснение (правило для ИИ или, если его нет, комментарий человека), а цитата,
 * если её вписали, обязана дословно найтись в транскрипте — сервер проверяет то
 * же и иначе отказал бы при сохранении. `quoteFound(text)` — предпроверка цитаты. */
export const validateCorrections = (criteria, scores, comments, kb, quoteFound) => {
    const reasonsRequired = [];
    const quotesInvalid = [];
    (criteria || []).forEach((c, i) => {
        if (!isCorrection(c, scores?.[i])) return;
        const extra = kb?.[c.idx] || {};
        if (!String(extra.rule || '').trim() && !String(comments?.[i] || '').trim()) reasonsRequired.push(i);
        const quote = String(extra.excerpt || '').trim();
        if (quote && !quoteFound(quote)) quotesInvalid.push(i);
    });
    return { reasonsRequired, quotesInvalid, ok: !reasonsRequired.length && !quotesInvalid.length };
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
    let penalty = 0;
    const total = criteria.reduce((sum, c, i) => {
        const v = scores[i];
        if (c.is_critical) {
            if (v === DEFICIENCY) penalty += criticalDeficiencyPenalty(c);
            return sum;
        }
        if (v === CORRECT || v === NOT_APPLICABLE) return sum + (Number(c.weight) || 0);
        if (v === DEFICIENCY && hasDeficiency(c)) return sum + (Number(c.deficiency.weight) || 0);
        return sum;
    }, 0);
    return Math.round(Math.max(0, total - penalty));
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
export const validationLabel = ({ missing, commentsRequired, reasonsRequired, quotesInvalid }) => {
    const parts = [];
    if (missing?.length) parts.push(`не проставлено: ${missing.length}`);
    if (commentsRequired?.length) parts.push(`нужен комментарий к ошибкам: ${commentsRequired.length}`);
    if (reasonsRequired?.length) parts.push(`объясните исправления: ${reasonsRequired.length}`);
    if (quotesInvalid?.length) parts.push(`цитаты нет в транскрипте: ${quotesInvalid.length}`);
    return parts.join(' · ');
};

/* Подписи вердиктов в карточке — те же слова, что оператор видит в «Моих
 * оценках» (src/components/evaluation/myEvaluationsPhone.js). */
export const VERDICT_LABEL = {
    [CORRECT]: 'Верно',
    [INCORRECT]: 'Неверно',
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
