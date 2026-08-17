/* Правила прохождения сценария обращения — без React, чтобы их можно было проверить.
 *
 * Модуль появился после разбора реальной ошибки. Готовность шага считалась прямо
 * в JSX одной строкой и смотрела только в answers, а выбранный файл лежал в
 * отдельном состоянии — из-за этого шаг с вложением НИКОГДА не становился
 * пройденным, и четыре тематики из шести нельзя было отправить вообще. Серверные
 * тесты этого поймать не могли: регламент на сервере был верный, ломался переход
 * по шагам. Логика, живущая в разметке, не проверяется ничем — поэтому она здесь.
 *
 * Правило разделения: тут решают, МОЖНО ли идти дальше и что сейчас показывать;
 * право сказать «отправляем» остаётся за сервером (crm/scenarios.py::evaluate).
 */

// Синтетические ключи сервера: они не соответствуют ни одному шагу, поэтому
// подсветить их «под вопросом» нельзя — им нужен свой адресат.
export const MISSING_ATTACHMENT = '__attachment__';
export const MISSING_CHECKS = '__checks__';

export const answerValue = (answers, key) => {
    const raw = (answers || {})[key];
    return raw && typeof raw === 'object' ? raw.value : raw;
};

export const isAnswered = (raw) => {
    if (raw === null || raw === undefined) return false;
    if (typeof raw === 'string') return raw.trim().length > 0;
    if (typeof raw === 'object') return Boolean(raw.value);
    return true;
};

export const stepIsVisible = (step, answers) => {
    if (!step || !step.depends_on) return true;
    return answerValue(answers, step.depends_on[0]) === step.depends_on[1];
};

export const visibleSteps = (scenario, answers) => (
    ((scenario && scenario.steps) || []).filter((step) => stepIsVisible(step, answers))
);

/* Пройден ли шаг настолько, чтобы пустить оператора дальше.
 *
 * Шаг с вложением — особый: его ответ живёт не в answers, а в выбранном файле.
 * Это и был источник ошибки, поэтому условие тут явное, а не «по умолчанию как
 * у всех остальных».
 */
export const stepIsComplete = (step, { answers = {}, attachment = null, scenario = null } = {}) => {
    if (!step) return false;
    if (step.kind === 'attachment') {
        if (attachment) return true;
        // Вложение не требуется тематикой — шаг проходится пустым.
        const required = scenario ? scenario.attachment !== 'none' : true;
        return !required || Boolean(step.optional);
    }
    if (isAnswered(answers[step.key])) {
        // «Да, и уточните» без уточнения — ещё не ответ.
        if (step.kind === 'yesno_date' && answerValue(answers, step.key) === 'yes') {
            const raw = answers[step.key];
            const detail = raw && typeof raw === 'object' ? raw.detail : '';
            return Boolean(detail && String(detail).trim());
        }
        return true;
    }
    return Boolean(step.optional);
};

/* Первое сработавшее правило сценария по текущим ответам.
 *
 * Порядок в списке правил — это приоритет: в ТЗ «сервис заработал» стоит первым
 * пунктом среди причин не отправлять, и он должен побеждать блокировки про
 * невыполненные проверки. Поэтому перебор идёт сверху вниз и останавливается.
 */
export const localVerdict = (scenario, answers) => {
    for (const rule of (scenario && scenario.rules) || []) {
        const [key, expected] = rule.when;
        if (answerValue(answers, key) === expected) return rule;
    }
    return null;
};

/* Куда вернуть оператора, когда сервер сказал «не хватает данных».
 *
 * Возвращает {phase, stepIndex, message}. Синтетические ключи обрабатываются
 * отдельно: без этого ответ «не приложено вложение» не показывался вообще —
 * поиск по ключам шагов давал -1, и мастер молчал.
 */
export const missingTarget = (steps, missing) => {
    const problems = missing || {};
    if (problems[MISSING_CHECKS]) {
        return { phase: 'checks', stepIndex: null, message: problems[MISSING_CHECKS] };
    }
    if (problems[MISSING_ATTACHMENT]) {
        const index = (steps || []).findIndex((step) => step.kind === 'attachment');
        return {
            phase: 'steps',
            stepIndex: index >= 0 ? index : null,
            message: problems[MISSING_ATTACHMENT],
        };
    }
    const index = (steps || []).findIndex((step) => problems[step.key]);
    if (index >= 0) {
        return { phase: 'steps', stepIndex: index, message: problems[steps[index].key] };
    }
    return { phase: 'steps', stepIndex: null, message: null };
};

/* Ответы, которые переносим при переходе в другую тематику: переспрашивать ИИН
 * и период после «это другая тематика» — гарантированное раздражение. */
export const CARRY_OVER = ['iin', 'period', 'park', 'device', 'browser'];

export const carryOver = (answers) => {
    const carried = {};
    for (const field of CARRY_OVER) {
        if ((answers || {})[field] !== undefined) carried[field] = answers[field];
    }
    return carried;
};
