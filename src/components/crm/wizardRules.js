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
 * Заголовок берётся из home_queue_title — группы ТЕМАТИКИ, а не из адреса
 * темы. С тех пор как отдельную тему можно увести в чужую группу, это разные
 * вещи: уведённая тема остаётся в своей тематике (там её ищут), а рядом с ней
 * строкой стоит настоящий адрес. Возьми мы адрес, тема выпрыгивала бы из
 * своего раздела при каждой настройке маршрута, и картотека перестраивалась
 * бы под оператором.
 *
 * Порядок групп — порядок первого появления в каталоге, то есть порядок,
 * заданный на сервере (crm/scenarios.py). Он же и порядок тематик внутри.
 */
export const groupCatalog = (catalog, entries = []) => {
    const byQueue = new Map((entries || []).map((entry) => [entry.queue_code, entry]));
    const groups = [];
    const byCode = new Map();
    for (const item of catalog || []) {
        // Тематика, в которую ведёт только проверка по ИИН, в списке выбора не
        // стоит: выбрать её оператор всё равно не может (инструкция #230, §3).
        if (item.entry_only) continue;
        const code = item.queue_code || '';
        const home = item.home_queue_title || item.queue_title || '';
        let group = byCode.get(code);
        if (!group) {
            group = { code, title: home, items: [] };
            byCode.set(code, group);
            groups.push(group);
        }
        // Название очереди приходит из базы и у невыгруженной очереди пустое —
        // берём первое непустое, чтобы заголовок не терялся из-за порядка.
        if (!group.title && home) group.title = home;
        group.items.push(item);
    }
    /* Очередь со входом занимает в списке ОДНУ строку — саму тематику. Категории
     * оператор увидит после проверки по ИИН, и раньше показывать их нельзя: он
     * выбирал бы, не имея данных, на которых этот выбор основан. */
    for (const group of groups) {
        const entry = byQueue.get(group.code);
        if (!entry) continue;
        group.entry = entry;
        group.items = [{
            key: `entry:${entry.queue_code}`,
            entry,
            title: entry.title,
            when_to_use: entry.when_to_use,
            queue_code: entry.queue_code,
            is_ready: entry.is_ready !== false,
        }];
        if (!group.title) group.title = entry.home_queue_title || entry.queue_title || '';
    }
    return groups;
};

/* Telegram-группа темы, если это НЕ группа её тематики. null — уйдёт «к себе»,
 * и писать это в строке не нужно: чат по умолчанию один на весь раздел
 * картотеки, а повторённый в каждой строке заголовок читать перестают.
 *
 * Отдельной функцией, а не условием в разметке: то же правило действует и в
 * списке тем, и в выборе категории после проверки по ИИН, — а одинаковое
 * правило, написанное в двух местах, расходится.
 */
export const routeNote = (item) => (
    item && item.routed && item.chat_title ? item.chat_title : null);

/* Подпись бейджа у темы, которую сейчас выбрать нельзя.
 *
 * У обычной темы это «Нет группы»: к её тематике не привязана Telegram-группа.
 * У уведённой группа как раз есть и названа строкой рядом — просто бота из неё
 * выгнали. «Нет группы» под строкой «Уйдёт в группу «Sapar/Kaspi»» читалось бы
 * как противоречие: то ли группы нет, то ли она есть и названа.
 */
export const blockedLabel = (item) => (
    item && item.routed ? 'Бот не в группе' : 'Нет группы');

/* Категории входа — в порядке, заданном сервером, и только настроенные.
 * Sapar промолчал (verdicts пуст) — показываем и «Документы не поступили»:
 * решить за оператора нечем, а работать надо. */
export const entryCategories = (entry, catalog, { withNoDocuments = false } = {}) => {
    const keys = [...((entry && entry.categories) || [])];
    if (withNoDocuments && entry && entry.no_documents) keys.push(entry.no_documents);
    const byKey = new Map((catalog || []).map((item) => [item.key, item]));
    return keys.map((key) => byKey.get(key)).filter(Boolean);
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

/* Офисы города — варианты вопроса «Адрес офиса».
 *
 * Они не из справочника фронта и не из options: список зависит от ответа на
 * предыдущий вопрос и приезжает вместе со статусами той самой проверкой,
 * которую требует §3.2 ТЗ. Поэтому источник у них один — снимок.
 *
 * Пустой массив у вопроса, который снимка ещё не дождался, — не ошибка:
 * оператор в этот момент выбирает город.
 */
export const officeOptions = (step, snapshot) => (
    step?.kind === 'office' ? (snapshot?.offices || []) : null
);

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
export const rowsOfGroup = (scenario, group, answers) => (
    pairRows(stepsOfGroup(scenario, group, answers))
);

/* Та же раскладка для произвольного списка вопросов: экран входа тоже показывает
 * «Таксопарк» и «Город» одной строкой, и правило пары должно быть одно на оба
 * места, а не переписано во втором. */
export const pairRows = (steps) => {
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

/* ─── Проверка по справочнику компании (ТЗ #201) ─────────────────────────── */

/* По каким ответам её спрашивают. Список приезжает с сервера вместе с
 * тематикой: мастер не знает ни названий тематик, ни того, что именно
 * проверяется, — иначе третья такая проверка означала бы третью правку
 * интерфейса. */
export const lookupInputs = (scenario) => (scenario?.lookup ? (scenario.lookup_inputs || []) : []);

/* Ключ проверки: те же ответы, склеенные в строку. По нему видно, что
 * спрашивать заново нечего — оператор вернулся назад и нажал «Далее» ещё раз. */
export const lookupKey = (scenario, answers) => (
    lookupInputs(scenario)
        .map((key) => String(answerValue(answers, key) ?? '').trim())
        .join('|')
);

export const lookupIsReady = (scenario, answers) => {
    const inputs = lookupInputs(scenario);
    return inputs.length > 0 && inputs.every((key) => isAnswered(answers?.[key]));
};

/* Спрашивать ли справочник прямо сейчас. Условия те же, что у Sapar: тематика
 * с проверкой, ответы для неё заполнены и это не та же самая пара, которую уже
 * спрашивали.
 *
 * Экран здесь не проверяется, в отличие от Sapar: у офисов ответ нужен, чтобы
 * нарисовать сам вопрос, и ждать ухода с экрана значило бы показать пустой
 * список. Когда спрашивать — говорит сама тематика (lookup_on_answer). */
export const needsLookup = (scenario, answers, checkedKey) => (
    lookupIsReady(scenario, answers) && lookupKey(scenario, answers) !== checkedKey
);

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
    /* Заголовки — словами инструкции #230, буква в букву. Оператор читает их и
     * на экране проверки, и потом рядом с вопросами: разные формулировки одного
     * и того же ответа читаются как два разных ответа. */
    if (!documents.length) {
        return {
            tone: 'amber',
            title: 'Нет документов. Документы не поступили',
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
        title: 'Есть документы за отчётный период',
        lines: [
            documents.length === 1 ? statuses.join(' · ')
                : `${documents.length} шт. · ${statuses.join(' · ')}`,
            snapshot.driver_name,
        ].filter(Boolean),
    };
};

/* ─── Порядок экранов мастера ─────────────────────────────────────────────── */

/* Чек-лист «Проверьте это до обращения» стоит ПОСЛЕ первого экрана вопросов,
 * а не перед ним (просьба владельца 21.08.2026).
 *
 * Причина в предпроверке Sapar: она запускается, как только введены ИИН и
 * период, и часть обращений на ней и заканчивается. Гонять оператора по
 * чек-листу до того, как выяснилось, что обращение вообще не нужно, — работа
 * впустую, а во время разговора с водителем это ещё и минута молчания.
 *
 * Правило одно на все тематики: у той, где Sapar не спрашивают, порядок тоже
 * «сначала кто и за какой период, потом проверки» — оператор запоминает одну
 * форму, а не две.
 */
export const CHECKS_AFTER_GROUP = 0;

export const hasChecks = (scenario) => Boolean(scenario?.checks?.length);

/* ─── Вход в тематику: проверка по ИИН до выбора категории ────────────────── */

/* Можно ли спрашивать Sapar: экран входа заполнен, ИИН и период — настоящие.
 * Формат проверяем здесь, а не только на сервере: иначе кнопка «Далее» уходит
 * в запрос, который заведомо вернётся ошибкой. */
export const entryIsComplete = (entry, answers) => {
    const steps = (entry && entry.steps) || [];
    if (!steps.length) return false;
    if (!steps.every((step) => stepIsComplete(step, { answers }))) return false;
    const iin = String(answerValue(answers, 'iin') ?? '').trim();
    const period = String(answerValue(answers, 'period') ?? '').trim();
    return IIN_PATTERN.test(iin) && PERIOD_PATTERN.test(period);
};

/* Первый экран, на котором ещё есть что заполнять.
 *
 * Нужен там, где часть ответов дана ДО выбора категории: ИИН, период, парк и
 * город оператор ввёл на входе, и показывать ему тот же экран второй раз —
 * лишний шаг. Заполнено всё — сразу к проверке ответов. */
export const openStop = (scenario, groups, state = {}) => {
    const index = (groups || []).findIndex(
        (group) => !groupIsComplete(scenario, group, state));
    return index >= 0 ? { phase: 'form', groupIndex: index } : { phase: 'submit' };
};

/* Куда вести после выбранной категории (инструкция #230, §2).
 *
 * Чек-лист снова стоит первым — и это не откат прежней правки, а её следствие:
 * его отодвигали за первый экран ради проверки по ИИН, а она теперь проходит
 * ещё до выбора категории. Порядок ровно как в инструкции: категория →
 * проверки → вопросы → подтверждение. */
export const afterCategory = (scenario, groups, state = {}) => (
    hasChecks(scenario) && !state.checksReady
        ? { phase: 'checks' }
        : openStop(scenario, groups, state)
);

/* Куда вести оператора с экрана вопросов. Возвращает:
 *   { phase: 'checks' }                 — показать чек-лист
 *   { phase: 'form', groupIndex: N }    — следующий экран вопросов
 *   { phase: 'submit' }                 — вопросы кончились, спрашиваем сервер
 */
export const nextStop = (scenario, groups, groupIndex, { checksReady = false } = {}) => {
    if (hasChecks(scenario) && !checksReady && groupIndex === CHECKS_AFTER_GROUP) {
        return { phase: 'checks' };
    }
    if (groupIndex + 1 < (groups || []).length) {
        return { phase: 'form', groupIndex: groupIndex + 1 };
    }
    return { phase: 'submit' };
};

/* Куда вести кнопкой «Назад». { phase: 'pick' } — к выбору тематики. */
export const previousStop = (scenario, groups, groupIndex) => {
    if (groupIndex <= 0) return { phase: 'pick' };
    if (hasChecks(scenario) && groupIndex === CHECKS_AFTER_GROUP + 1) {
        return { phase: 'checks' };
    }
    return { phase: 'form', groupIndex: groupIndex - 1 };
};

/* Куда вести после подтверждённого чек-листа. Экранов может и не остаться —
 * тогда сразу к проверке ответов, а не в пустой шаг.
 *
 * resume — маршрут для входа по ИИН: там чек-лист стоит ПЕРЕД вопросами, и
 * возвращаться после него надо на первый незаполненный экран, а не на второй по
 * счёту. Не передали — прежний порядок, буква в букву. */
export const afterChecks = (groups, resume = null) => (
    resume || (
        (groups || []).length > CHECKS_AFTER_GROUP + 1
            ? { phase: 'form', groupIndex: CHECKS_AFTER_GROUP + 1 }
            : { phase: 'submit' })
);

/* ─── Отчётный период одним списком ───────────────────────────────────────── */

export const MONTH_NAMES = [
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

/* Сколько периодов показываем. Двух лет хватает с запасом: в Sapar самые старые
 * документы — за март 2026, а разбираться с позапрошлогодними никто не станет. */
export const PERIOD_DEPTH = 24;

/* Периоды для выбора: сначала ПРОШЛЫЙ месяц, дальше вглубь.
 *
 * Раньше это были два списка — месяц с января и год, — и оператору приходилось
 * листать до нужного. Между тем нужный почти всегда один: отчётный период это
 * месяц, ЗА который документы, а документы за июль подписывают в августе.
 *
 * Текущего месяца в списке нет намеренно. Он не может быть отчётным периодом —
 * месяц ещё не закончился, документов за него нет ни у кого, — и именно на нём
 * операторы путались, принимая «месяц, когда жду документы» за «месяц, за
 * который они». Серверная проверка на этот случай осталась: список выбора это
 * подсказка, а не защита.
 */
export const periodOptions = (today = new Date(), depth = PERIOD_DEPTH) => {
    const options = [];
    // Первый шаг назад — это и есть прошлый месяц: у Date месяцы с нуля,
    // поэтому getMonth() уже указывает на предыдущий в человеческом счёте.
    let year = today.getFullYear();
    let month = today.getMonth();          // 1..12 предыдущего месяца
    if (month === 0) { month = 12; year -= 1; }
    for (let index = 0; index < depth; index += 1) {
        options.push({
            value: `${year}-${String(month).padStart(2, '0')}`,
            label: `${MONTH_NAMES[month - 1]} ${year}`,
        });
        month -= 1;
        if (month === 0) { month = 12; year -= 1; }
    }
    return options;
};

/* Что показать в поле, когда там уже лежит период — в том числе такой, которого
 * в списке нет (старое обращение, ответ из другой тематики). */
export const periodLabel = (value) => {
    const match = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(String(value || ''));
    return match ? `${MONTH_NAMES[Number(match[2]) - 1]} ${match[1]}` : '';
};
