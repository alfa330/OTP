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

// Расширение в пути обязательно: модуль проверяется `node --test`, а его
// ESM-резолвер, в отличие от Vite, безрасширочный путь не находит.
import { KAZAKHSTAN_CITY_OPTIONS } from '../../utils/kazakhstanCities.js';

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
 * и период после «это другая тематика» — гарантированное раздражение. Город тут
 * по той же причине, что и парк: это «где», а «где» от смены тематики не
 * меняется. */
export const CARRY_OVER = ['iin', 'period', 'park', 'city', 'device', 'browser'];

export const carryOver = (answers) => {
    const carried = {};
    for (const field of CARRY_OVER) {
        if ((answers || {})[field] !== undefined) carried[field] = answers[field];
    }
    return carried;
};

/* ─── Чек-лист перед обращением ───────────────────────────────────────────── */

/* Пройден ли чек-лист.
 *
 * Тематики Sapar подтверждают проверки одной галочкой: их до восьми, и восемь
 * нажатий вместо одного никто не просил. У термокороба ТЗ требует отмечать
 * КАЖДЫЙ пункт отдельно — поэтому режим несёт тематика (checks_each), а не тип
 * и не интерфейс.
 *
 * Тематика без чек-листа проходит его пустым: экрана проверок у неё нет вовсе.
 */
export const checksAreComplete = (
    scenario, { confirmedAll = false, confirmedItems = [] } = {},
) => {
    const total = ((scenario && scenario.checks) || []).length;
    if (!total) return true;
    if (!scenario.checks_each) return Boolean(confirmedAll);
    const done = new Set((confirmedItems || []).map(Number));
    for (let index = 0; index < total; index += 1) {
        if (!done.has(index)) return false;
    }
    return true;
};

/* Что отправить серверу о чек-листе. Оба поля сразу: сервер приводит их к одному
 * множеству отмеченных пунктов, и решение всё равно принимает он. */
export const checksPayload = (scenario, { confirmedAll = false, confirmedItems = [] } = {}) => ({
    checks_confirmed: Boolean(scenario && scenario.checks_each ? false : confirmedAll),
    checks_done: scenario && scenario.checks_each
        ? [...new Set((confirmedItems || []).map(Number))].sort((a, b) => a - b)
        : [],
});

/* Переключить один пункт чек-листа. Возвращает НОВЫЙ массив — состояние в React
 * менять на месте нельзя. */
export const toggleCheck = (confirmedItems, index) => {
    const done = new Set((confirmedItems || []).map(Number));
    if (done.has(index)) done.delete(index); else done.add(index);
    return [...done].sort((a, b) => a - b);
};

/* ─── Картотека тематик по группам ────────────────────────────────────────── */

/* Тематики в выборе «Новое обращение» сгруппированы по тому, КУДА уйдёт
 * обращение: у каждой группы своя рабочая Telegram-группа и свои люди на той
 * стороне. Плоским списком (как было) оператор выбирает тематику, не понимая,
 * кого он этим побеспокоит, а с ростом числа тематик список превращается в
 * простыню.
 *
 * Группируем по очереди, а не по отдельному полю «раздел»: очередь и есть
 * адресат, и её название сотрудник видит потом в списке обращений. Второе поле
 * с тем же смыслом означало бы два ответа на один вопрос.
 *
 * Порядок групп — порядок первого появления в каталоге, то есть порядок,
 * заданный на сервере (crm/scenarios.py). Он же и порядок тематик внутри.
 */
export const groupCatalog = (catalog) => {
    const groups = [];
    const byCode = new Map();
    for (const item of catalog || []) {
        const code = item.queue_code || '';
        let group = byCode.get(code);
        if (!group) {
            group = { code, title: item.queue_title || '', items: [] };
            byCode.set(code, group);
            groups.push(group);
        }
        // Название очереди приходит из базы и у невыгруженной очереди пустое —
        // берём первое непустое, чтобы заголовок не терялся из-за порядка.
        if (!group.title && item.queue_title) group.title = item.queue_title;
        group.items.push(item);
    }
    return groups;
};

/* ─── Экраны вместо отдельных вопросов ────────────────────────────────────── */

/* Порядок экранов приходит с сервера (scenario.groups): он одинаков во всех
 * тематиках, чтобы оператор не искал каждый раз, с чего тут начинают. */
export const groupsOf = (scenario, answers) => {
    const present = new Set(visibleSteps(scenario, answers).map((step) => step.group));
    return ((scenario && scenario.groups) || []).filter((name) => present.has(name));
};

export const stepsOfGroup = (scenario, group, answers) => (
    visibleSteps(scenario, answers).filter((step) => step.group === group)
);

/* Экран пройден, когда пройдены все его вопросы. Кнопка «Далее» смотрит сюда,
 * а не на один текущий вопрос. */
export const groupIsComplete = (scenario, group, { answers = {}, attachment = null } = {}) => (
    stepsOfGroup(scenario, group, answers)
        .every((step) => stepIsComplete(step, { answers, attachment, scenario }))
);

/* Варианты для вопроса, который спрашивает значение из справочника.
 *
 * Справочники разные по природе, и это видно здесь: парки приходят с сервера
 * (их заводит вики, они меняются без нас), города лежат рядом в интерфейсе —
 * это справочник Казахстана, к нашей базе он отношения не имеет и незачем
 * гонять его по сети на каждое открытие раздела.
 *
 * null означает «вопрос не из справочника» — рисуем обычным полем.
 */
export const referenceOptions = (step, { taxiParks = [] } = {}) => {
    if (!step) return null;
    if (step.kind === 'taxi_park') {
        return taxiParks.map((name) => ({ value: name, label: name }));
    }
    if (step.kind === 'city') return KAZAKHSTAN_CITY_OPTIONS;
    return null;
};

/* Вопросы экрана, разложенные по строкам.
 *
 * Нужно ровно для одного: «Таксопарк» и «Город» — это одно «где», а не два
 * разных вопроса, и стоять они должны рядом, занимая столько же места, сколько
 * занимало прежнее общее поле. Признак несёт сам вопрос (step.half), поэтому
 * раскладка не зашита в разметку и не знает про конкретные ключи.
 *
 * Одинокий половинный вопрос занимает всю ширину: поле в пол-экрана без пары
 * выглядит обрезанным. Так бывает у посылок — там город есть, а парка нет.
 * Вложение в пару не берём никогда: у него своя механика выбора файла.
 */
export const rowsOfGroup = (scenario, group, answers) => {
    const steps = stepsOfGroup(scenario, group, answers);
    const rows = [];
    for (let index = 0; index < steps.length; index += 1) {
        const step = steps[index];
        const next = steps[index + 1];
        const pairable = (item) => Boolean(item) && item.half && item.kind !== 'attachment';
        if (pairable(step) && pairable(next)) {
            rows.push([step, next]);
            index += 1;
        } else {
            rows.push([step]);
        }
    }
    return rows;
};

/* На какой экран вернуть, когда сервер сказал «не хватает данных».
 * Возвращает {phase, group, message}. */
export const missingGroup = (scenario, answers, missing) => {
    const problems = missing || {};
    if (problems[MISSING_CHECKS]) {
        return { phase: 'checks', group: null, message: problems[MISSING_CHECKS] };
    }
    const steps = visibleSteps(scenario, answers);
    if (problems[MISSING_ATTACHMENT]) {
        const step = steps.find((item) => item.kind === 'attachment');
        return {
            phase: 'form',
            group: step ? step.group : null,
            message: problems[MISSING_ATTACHMENT],
        };
    }
    const step = steps.find((item) => problems[item.key]);
    return {
        phase: 'form',
        group: step ? step.group : null,
        message: step ? problems[step.key] : null,
    };
};

/* ─── Предпроверка по Sapar ───────────────────────────────────────────────── */

/* Экран, после которого имеет смысл спросить Sapar: тот, где стоит ИИН.
 *
 * Не «первый экран» и не номер: экраны у вопроса заданы по ключу и у разных
 * тематик идут в разном составе. Привязка к самому вопросу переживёт любую
 * перестановку.
 */
export const saparGroup = (scenario) => {
    if (!scenario?.sapar) return null;
    const step = (scenario.steps || []).find((item) => item.key === 'iin');
    return step ? step.group : null;
};

/* Ключ проверки: пара «ИИН + период». По нему видно, что спрашивать заново
 * нечего — оператор просто вернулся на шаг назад и нажал «Далее» ещё раз.
 * Лишний запрос тут не страшен, но и полсекунды ожидания на ровном месте
 * оператору не нужны. */
export const saparKey = (answers) => {
    const iin = String(answerValue(answers, 'iin') ?? '').trim();
    const period = String(answerValue(answers, 'period') ?? '').trim();
    return iin && period ? `${iin}|${period}` : '';
};

export const IIN_PATTERN = /^[0-9]{12}$/;
export const PERIOD_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;

/* Спрашивать ли Sapar прямо сейчас.
 *
 * Условий четыре, и каждое из них однажды было причиной лишнего запроса:
 * тематика без предпроверки, другой экран, недозаполненные ИИН или период,
 * и та же пара, которую уже спросили.
 */
export const needsSaparCheck = (scenario, group, answers, checkedKey) => {
    if (!scenario?.sapar || !group || group !== saparGroup(scenario)) return false;
    const iin = String(answerValue(answers, 'iin') ?? '').trim();
    const period = String(answerValue(answers, 'period') ?? '').trim();
    if (!IIN_PATTERN.test(iin) || !PERIOD_PATTERN.test(period)) return false;
    return saparKey(answers) !== checkedKey;
};

/* Снимок Sapar → как его показать оператору.
 *
 * tone решает не только цвет: «документов нет» и «документы есть» это
 * противоположные ответы, и одинаково серыми их показывать нельзя.
 */
export const describeSnapshot = (snapshot) => {
    if (!snapshot || !snapshot.available) {
        return {
            tone: 'muted',
            title: 'Sapar не ответил',
            lines: ['Проверьте данные сами и продолжайте по вопросам.'],
        };
    }
    const documents = snapshot.documents || [];
    if (!documents.length) {
        return {
            tone: 'amber',
            title: 'Документов за период нет',
            lines: snapshot.month_ready === false
                ? ['Выгрузка за месяц по парку ещё не сформирована.']
                : ['У водителя за этот период документов в Sapar не найдено.'],
        };
    }
    const statuses = [];
    for (const document of documents) {
        const label = document.status_label || 'статус неизвестен';
        if (!statuses.includes(label)) statuses.push(label);
    }
    return {
        tone: 'green',
        title: documents.length === 1
            ? 'Документ за период найден'
            : `Документов за период: ${documents.length}`,
        lines: [statuses.join(' · '), snapshot.driver_name].filter(Boolean),
    };
};
