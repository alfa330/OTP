/*
 * «Учет часов» на телефоне — правила без React: месяц, форматы чисел, ячейки
 * календаря оператора, строка списка и итоги раздела.
 *
 * Настольный вид — одна широкая таблица: слева имя со ставкой и нормой, дальше
 * тридцать одна колонка дней, справа две-четыре колонки итогов, и всё это
 * листается вбок внутри страницы. На экране 390 px от неё видно два столбца, а
 * горизонтальная прокрутка внутри вертикальной — самый неудобный жест, какой
 * бывает. Поэтому на телефоне раздел разложен по уровням: список операторов с
 * ОДНИМ числом выбранного показателя → месяц оператора ячейками → день с
 * правкой. Числа те же самые: формулы строки и подвала повторяют
 * HoursAccountingView в App.jsx один в один.
 *
 * Здесь только арифметика и подписи — разметка в HoursAccountingMobile.jsx,
 * данные и запросы остаются в App.jsx. Помощники, считающие длительности
 * тренингов, техсбоев и офлайн-активности, приходят аргументом `helpers`: это
 * функции раздела, и вторая их копия разъехалась бы с настольной на первой же
 * правке правил.
 */

export const HOURS_PHONE_WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/* Сколько месяцев назад листается шапка. Настольный вид берёт <input
   type="month"> без границ, но список в колесе iOS обязан быть конечным:
   два года назад и месяц вперёд закрывают и сверку прошлых периодов, и
   проставленную заранее норму. */
export const HOURS_PHONE_MONTHS_BACK = 23;
export const HOURS_PHONE_MONTHS_FORWARD = 1;

const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const WEEKDAYS_FULL = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];

const pad2 = (value) => String(value).padStart(2, '0');
const finite = (value) => {
    const num = Number(value);
    return Number.isFinite(num) ? num : 0;
};
const isMap = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const asArray = (value) => (Array.isArray(value) ? value : []);

const RU_2 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const RU_1 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const RU_0 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

/* `|| 0` снимает минус у нуля: -0,001 после округления печатался бы «-0». */
export const formatHoursPhoneNumber = (value) => RU_2.format(Math.round(finite(value) * 100) / 100 || 0);
export const formatHoursPhoneHours = (value) => `${formatHoursPhoneNumber(value)} ч`;
export const formatHoursPhoneCount = (value) => RU_0.format(Math.round(finite(value)) || 0);
export const formatHoursPhonePercent = (value) => (
    value === null || value === undefined || !Number.isFinite(Number(value))
        ? '—'
        : `${RU_1.format(Math.round(finite(value) * 10) / 10 || 0)} %`
);
export const formatHoursPhoneMoney = (value) => `${RU_0.format(Math.round(finite(value)) || 0)} ₸`;

/* Деньги в ячейке дня: семь колонок на 390 px дают 45 px под число, и «10 000 ₸»
   там не живёт ни при каком кегле. Полная сумма остаётся на экране дня. */
export const formatHoursPhoneCompactMoney = (value) => {
    const num = Math.round(finite(value));
    if (!num) return '';
    const sign = num < 0 ? '-' : '';
    const abs = Math.abs(num);
    if (abs >= 1000) {
        const thousands = abs / 1000;
        return `${sign}${RU_1.format(Math.round(thousands * 10) / 10)}к`;
    }
    return `${sign}${RU_0.format(abs)}`;
};

const parseMonth = (value) => {
    const match = /^(\d{4})-(\d{2})$/.exec(String(value || ''));
    if (!match) return null;
    const monthIndex = Number(match[2]) - 1;
    if (monthIndex < 0 || monthIndex > 11) return null;
    return { year: Number(match[1]), monthIndex };
};

export const formatHoursPhoneMonth = (value) => {
    const parsed = parseMonth(value);
    return parsed ? `${MONTHS[parsed.monthIndex]} ${parsed.year}` : '';
};

export const shiftHoursPhoneMonth = (value, delta, today = new Date()) => {
    const parsed = parseMonth(value);
    if (!parsed) return null;
    const index = parsed.year * 12 + parsed.monthIndex + delta;
    const newest = today.getFullYear() * 12 + today.getMonth() + HOURS_PHONE_MONTHS_FORWARD;
    if (index > newest || index < newest - HOURS_PHONE_MONTHS_FORWARD - HOURS_PHONE_MONTHS_BACK) return null;
    return `${Math.floor(index / 12)}-${pad2((index % 12) + 1)}`;
};

/* Список для колеса месяцев. Выбранный месяц добавляется, даже если он вне
   окна: раздел мог открыться по ссылке или пережить смену суток, и колесо без
   текущего значения показало бы чужой месяц. */
export const hoursPhoneMonthOptions = (month, today = new Date()) => {
    const newest = today.getFullYear() * 12 + today.getMonth() + HOURS_PHONE_MONTHS_FORWARD;
    const options = [];
    for (let index = newest; index > newest - HOURS_PHONE_MONTHS_FORWARD - HOURS_PHONE_MONTHS_BACK - 1; index -= 1) {
        const key = `${Math.floor(index / 12)}-${pad2((index % 12) + 1)}`;
        options.push({ value: key, label: formatHoursPhoneMonth(key) });
    }
    if (parseMonth(month) && !options.some((option) => option.value === month)) {
        options.unshift({ value: month, label: formatHoursPhoneMonth(month) });
    }
    return options;
};

export const formatHoursPhoneDay = (month, day) => {
    const parsed = parseMonth(month);
    if (!parsed) return '';
    const date = new Date(parsed.year, parsed.monthIndex, Number(day) || 1);
    return `${date.getDate()} ${MONTHS_GENITIVE[date.getMonth()]}, ${WEEKDAYS_FULL[date.getDay()].toLowerCase()}`;
};

export const hoursPhoneDateStr = (month, day) => `${month}-${pad2(day)}`;

/* ── Тона ячеек ──────────────────────────────────────────────────────────────

   Настольная таблица красит ячейку непрерывной прозрачностью (alpha от 0.18 до
   0.95). На телефоне ячейка втрое меньше, и соседние оттенки одного зелёного
   там неразличимы — поэтому та же шкала разбита на четыре ступени. Пороги те
   же: доля от максимума колонки. */
const TONE_STEPS = {
    green: ['bg-green-100 text-green-900', 'bg-green-300 text-green-900', 'bg-green-500 text-white', 'bg-green-700 text-white'],
    amber: ['bg-amber-100 text-amber-900', 'bg-amber-200 text-amber-900', 'bg-amber-400 text-amber-950', 'bg-amber-500 text-white'],
    violet: ['bg-violet-100 text-violet-900', 'bg-violet-200 text-violet-900', 'bg-violet-400 text-white', 'bg-violet-600 text-white'],
    rose: ['bg-rose-100 text-rose-900', 'bg-rose-200 text-rose-900', 'bg-rose-400 text-white', 'bg-rose-600 text-white'],
    blue: ['bg-blue-100 text-blue-900', 'bg-blue-200 text-blue-900', 'bg-blue-400 text-white', 'bg-blue-600 text-white'],
};

export const HOURS_PHONE_EMPTY_TONE = 'bg-slate-100 text-slate-400';
export const HOURS_PHONE_FUTURE_TONE = 'text-slate-300';
export const HOURS_PHONE_LOCKED_TONE = 'bg-slate-200 text-slate-400';

export const hoursPhoneTone = (value, max, hue = 'green') => {
    const steps = TONE_STEPS[hue] || TONE_STEPS.green;
    const ratio = finite(max) > 0 ? Math.max(0, Math.min(1, finite(value) / finite(max))) : 0;
    if (ratio <= 0.25) return steps[0];
    if (ratio <= 0.5) return steps[1];
    if (ratio <= 0.75) return steps[2];
    return steps[3];
};

/* Эффективность (OCC) красится не долей от максимума, а самим процентом —
   ровно как настольный градиент «красный → янтарь → зелёный». */
export const hoursPhoneOccTone = (percent) => {
    const pct = finite(percent);
    if (pct >= 75) return 'bg-green-600 text-white';
    if (pct >= 50) return 'bg-amber-400 text-amber-950';
    return 'bg-rose-500 text-white';
};

/* Цвет процента выполнения плана успешек — пороги настольного planClosureClass. */
export const hoursPhonePlanTone = (percent) => {
    if (percent == null || !Number.isFinite(Number(percent))) return 'text-slate-400';
    if (percent >= 100) return 'text-emerald-700';
    if (percent >= 60) return 'text-amber-600';
    return 'text-rose-600';
};

/* Цвет выработки — пороги настольного productionColorClass. */
export const hoursPhoneProductionTone = (diff, norm) => {
    if (finite(norm) <= 0) return finite(diff) < 0 ? 'text-rose-600' : 'text-slate-500';
    if (finite(diff) < 0) return 'text-rose-600';
    return (finite(diff) / finite(norm)) * 100 < 10 ? 'text-amber-600' : 'text-emerald-700';
};

export const HOURS_PHONE_MARKERS = {
    training: { label: 'Тренинг', className: 'bg-yellow-400' },
    technical: { label: 'Тех. сбой', className: 'bg-violet-500' },
    offline: { label: 'Офлайн', className: 'bg-emerald-500' },
    fines: { label: 'Штраф', className: 'bg-red-400' },
};
const MARKER_ORDER = ['training', 'technical', 'offline', 'fines'];

/* Потолок заливки для денег — тот же, что в настольной ячейке (maxBonuses/maxFines). */
const MONEY_SCALE = 10000;

/* ── Помощники ───────────────────────────────────────────────────────────── */

const defaultHelpers = {
    trainingHours: () => 0,
    technicalHours: () => 0,
    offlineHours: () => 0,
    noPhoneHours: () => 0,
};

const dayEntry = (op, day) => (isMap(op?.daily) ? op.daily[String(day)] : null) || null;

const sumTechnical = (items, helpers) => asArray(items).reduce((acc, item) => acc + finite(helpers.technicalHours(item)), 0);
const sumOffline = (items, helpers) => asArray(items).reduce((acc, item) => acc + finite(helpers.offlineHours(item)), 0);

const dayFines = (entry) => (Array.isArray(entry?.fines) ? entry.fines : []);
const dayBonuses = (entry) => (Array.isArray(entry?.bonuses) ? entry.bonuses : []);

const finesSum = (entry) => {
    const list = dayFines(entry);
    if (list.length) return list.reduce((acc, item) => acc + finite(item.amount ?? item.fine_amount), 0);
    return finite(entry?.fine_amount);
};
const bonusesSum = (entry) => dayBonuses(entry).reduce((acc, item) => acc + finite(item.amount), 0);

/*
 * Итоги оператора за месяц — те же выражения, что в строке настольной таблицы.
 * Считаются один раз и разливаются по трём местам: строка списка, шапка экрана
 * оператора и подписи под календарём.
 */
export const buildHoursPhoneOperatorTotals = ({
    op,
    month = '',
    trainingsByDay = null,
    technicalByDay = null,
    offlineByDay = null,
    successesByDay = null,
    isChatModel = false,
    helpers = defaultHelpers,
}) => {
    const aggregates = isMap(op?.aggregates) ? op.aggregates : {};
    const regular = finite(aggregates.regular_hours);
    const norm = finite(op?.norm_hours);

    let trainingAll = 0;
    let trainingCounted = 0;
    let trainingNotCounted = 0;
    for (const key of Object.keys(isMap(trainingsByDay) ? trainingsByDay : {})) {
        const list = asArray(trainingsByDay[key]);
        trainingAll += finite(helpers.trainingHours(list));
        trainingCounted += finite(helpers.trainingHours(list, (item) => item && item.count_in_hours !== false));
        trainingNotCounted += finite(helpers.trainingHours(list, (item) => item && item.count_in_hours === false));
    }
    let technical = 0;
    for (const key of Object.keys(isMap(technicalByDay) ? technicalByDay : {})) {
        technical += sumTechnical(technicalByDay[key], helpers);
    }
    let offline = 0;
    for (const key of Object.keys(isMap(offlineByDay) ? offlineByDay : {})) {
        offline += sumOffline(offlineByDay[key], helpers);
    }

    const daily = isMap(op?.daily) ? op.daily : {};
    let noPhone = 0;
    let fines = 0;
    let bonuses = 0;
    let breakTime = 0;
    for (const key of Object.keys(daily)) {
        const entry = daily[key] || {};
        noPhone += finite(helpers.noPhoneHours(entry));
        fines += finesSum(entry);
        bonuses += bonusesSum(entry);
        breakTime += finite(entry.break_time);
    }

    const displayedTotal = regular + trainingCounted + technical + offline;
    const production = displayedTotal - norm;

    const metricTotal = (aggregateKey, dailyKey) => {
        const value = aggregates[aggregateKey];
        if (value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))) return Number(value);
        return Object.values(daily).reduce((sum, entry) => sum + finite(entry?.[dailyKey]), 0);
    };

    /* КВЗ считается на базовых часах ТОЛЬКО тех дней, что относятся к модели
       текущего вида: у переведённого посреди месяца иначе занижается. Ветка
       дословно повторяет настольную (effectiveCallHours). */
    const callHours = (() => {
        const whole = Math.max(0, regular);
        const segments = asArray(op?.group_segments);
        if (segments.length <= 1) return whole;
        const matched = segments.filter((segment) => (
            (String(segment?.calculation_model_code || '').trim() === 'chat_manager') === Boolean(isChatModel)
        ));
        if (!matched.length || matched.length === segments.length) return whole;
        let hours = 0;
        for (const segment of matched) {
            const start = Number(segment.start_day);
            const end = Number(segment.end_day);
            if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
            for (let day = start; day <= end; day += 1) {
                hours += finite(daily[String(day)]?.work_time);
            }
        }
        return hours > 0 ? hours : whole;
    })();

    const successes = Object.values(isMap(successesByDay) ? successesByDay : {})
        .reduce((acc, value) => acc + finite(value), 0);

    return {
        month,
        regular,
        norm,
        rate: op?.rate ?? null,
        trainingAll,
        trainingCounted,
        trainingNotCounted,
        technical,
        offline,
        noPhone,
        breakTime,
        fines,
        bonuses,
        displayedTotal,
        production,
        normPercent: norm > 0 ? (displayedTotal / norm) * 100 : null,
        calls: metricTotal('total_calls', 'calls'),
        dialTime: metricTotal('total_dial_time', 'dial_time'),
        talkTime: metricTotal('total_talk_time', 'talk_time'),
        chats: metricTotal('total_chats', 'chats'),
        efficiency: finite(aggregates.total_efficiency_hours),
        callHours,
        occ: regular > 0 ? Math.max(0, Math.min(100, (finite(aggregates.total_efficiency_hours) / regular) * 100)) : null,
        successes,
    };
};

/*
 * Число, которое стоит в строке оператора и крупно на его экране. Это первая
 * итоговая колонка настольной таблицы для выбранного показателя — та, ради
 * которой супервайзер и открывает вкладку.
 */
export const hoursPhoneOperatorValue = ({
    tab = 'work_time',
    totals,
    plan = null,
    isChatModel = false,
    isTezOpContext = false,
    chatAverage = null,
    responseAverage = null,
}) => {
    const t = totals || {};
    switch (tab) {
        case 'work_time':
            return { text: formatHoursPhoneHours(t.displayedTotal), caption: 'Итого часов' };
        case 'break_time':
            return { text: formatHoursPhoneHours(t.breakTime), caption: 'Перерыв за месяц' };
        case 'calls':
            if (isTezOpContext) return { text: formatHoursPhoneCount(t.calls), caption: 'Всего звонков' };
            /* То же число, что в правой колонке настольной таблицы: обращения
               за месяц ÷ базовые часы своей модели. Подпись здесь честнее
               настольной («Кол-во чатов» над частным — опечатка шапки). */
            return {
                text: t.callHours > 0 ? formatHoursPhoneNumber(t.calls / t.callHours) : '—',
                caption: isChatModel ? 'Чатов в час' : 'КВЗ, звонков в час',
            };
        case 'dial_time':
            return { text: formatHoursPhoneHours(t.dialTime), caption: 'Время набора' };
        case 'talk_time':
            return { text: formatHoursPhoneHours(t.talkTime), caption: 'В разговоре' };
        case 'chats':
            return { text: formatHoursPhoneCount(t.chats), caption: 'Всего чатов' };
        case 'tez_successes':
            return { text: formatHoursPhoneCount(t.successes), caption: 'Успешки за месяц' };
        case 'avg_score':
            return { text: chatAverage == null ? '—' : formatHoursPhoneNumber(chatAverage), caption: 'Средняя оценка' };
        case 'response_time':
            return { text: responseAverage == null ? '—' : `${formatHoursPhoneCount(responseAverage)} с`, caption: 'Ср. время ответа' };
        case 'efficiency':
            return { text: formatHoursPhoneHours(t.efficiency), caption: 'Эффективность' };
        case 'trainings':
            return { text: formatHoursPhoneHours(t.trainingAll), caption: 'Тренинги за месяц' };
        case 'technical_issues':
            return { text: formatHoursPhoneHours(t.technical), caption: 'Тех. сбои за месяц' };
        case 'offline_activity':
            return { text: formatHoursPhoneHours(t.offline), caption: 'Офлайн активность' };
        case 'no_phone':
            return { text: formatHoursPhoneHours(t.noPhone), caption: 'Без телефона' };
        case 'bonuses':
            return { text: formatHoursPhoneMoney(t.bonuses), caption: 'Бонусы за месяц' };
        case 'fines':
            return { text: formatHoursPhoneMoney(t.fines), caption: 'Штрафы за месяц' };
        default:
            return { text: '—', caption: '' };
    }
};

/*
 * Остальные итоговые колонки вкладки — строками под крупным числом на экране
 * оператора. Пустых строк не рисуем: «—» в списке настроек читается как
 * поломка, а не как «показателя нет».
 */
export const hoursPhoneOperatorRows = ({
    tab = 'work_time',
    totals,
    plan = null,
    isTezOpContext = false,
}) => {
    const t = totals || {};
    const rows = [];
    if (tab === 'work_time') {
        rows.push({ key: 'regular', label: 'База часов', value: formatHoursPhoneHours(t.regular) });
        rows.push({ key: 'norm', label: 'Вып. нормы', value: formatHoursPhonePercent(t.normPercent) });
        rows.push({
            key: 'production',
            label: 'Выработка',
            value: formatHoursPhoneHours(t.production),
            valueClassName: hoursPhoneProductionTone(t.production, t.norm),
        });
    }
    if (tab === 'efficiency') {
        rows.push({ key: 'occ', label: 'OCC', value: formatHoursPhonePercent(t.occ) });
    }
    if (tab === 'trainings' && t.trainingNotCounted > 0) {
        rows.push({ key: 'counted', label: 'Засчитано в часы', value: formatHoursPhoneHours(t.trainingCounted) });
        rows.push({ key: 'not-counted', label: 'Не засчитано', value: formatHoursPhoneHours(t.trainingNotCounted) });
    }
    if (tab === 'tez_successes') {
        const percent = plan > 0 ? (t.successes / plan) * 100 : null;
        rows.push({ key: 'plan', label: 'План успешек', value: plan > 0 ? RU_1.format(Math.round(plan * 10) / 10) : '—' });
        rows.push({
            key: 'closure',
            label: 'Выполнение',
            value: percent == null ? '—' : `${RU_0.format(Math.round(percent))} %`,
            valueClassName: hoursPhonePlanTone(percent),
        });
        rows.push({
            key: 'per-hour',
            label: 'Успешки в час',
            value: t.displayedTotal > 0 ? formatHoursPhoneNumber(t.successes / t.displayedTotal) : '—',
        });
    }
    if (tab === 'calls' && isTezOpContext) {
        rows.push({
            key: 'per-hour',
            label: 'Звонков в час',
            value: t.callHours > 0 ? formatHoursPhoneNumber(t.calls / t.callHours) : '—',
        });
    }
    return rows;
};

/*
 * Итог раздела — подвал настольной таблицы. Крупное число то же, что в строке
 * оператора, остальные колонки строками.
 */
export const buildHoursPhoneSectionTotals = ({
    tab = 'work_time',
    footer = {},
    isChatModel = false,
    isTezOpContext = false,
}) => {
    const f = footer || {};
    const rows = [];
    let value = '—';
    let caption = '';

    switch (tab) {
        case 'work_time':
            value = formatHoursPhoneHours(f.sumDisplayedTotal);
            caption = 'Итого часов';
            rows.push({ key: 'regular', label: 'База часов', value: formatHoursPhoneHours(f.sumRegular) });
            rows.push({
                key: 'norm-pct',
                label: 'Вып. нормы',
                value: finite(f.sumNorm) > 0 ? formatHoursPhonePercent((finite(f.sumDisplayedTotal) / finite(f.sumNorm)) * 100) : '—',
            });
            rows.push({
                key: 'production',
                label: 'Выработка',
                value: formatHoursPhoneHours(f.sumProd),
                valueClassName: hoursPhoneProductionTone(f.sumProd, f.sumNorm),
            });
            break;
        case 'break_time':
            value = formatHoursPhoneHours(f.sumBreakTime);
            caption = 'Перерыв';
            break;
        case 'calls':
            /* КВЗ по отделу настольный подвал не считает — среднее по строкам
               там означало бы «час оператора с тремя сменами весит столько же,
               сколько час оператора с двадцатью». Не выдумываем его и здесь. */
            if (isTezOpContext) {
                value = formatHoursPhoneCount(f.sumCalls);
                caption = 'Всего звонков';
            }
            break;
        case 'dial_time':
            value = formatHoursPhoneHours(f.sumDialTime);
            caption = 'Время набора';
            break;
        case 'talk_time':
            value = formatHoursPhoneHours(f.sumTalkTime);
            caption = 'В разговоре';
            break;
        case 'chats':
            value = formatHoursPhoneCount(f.sumChats);
            caption = 'Всего чатов';
            break;
        case 'tez_successes': {
            const plan = f.hasTezPlanRows ? finite(f.sumTezPlan) : null;
            const percent = plan > 0 ? (finite(f.sumTezSuccesses) / plan) * 100 : null;
            value = formatHoursPhoneCount(f.sumTezSuccesses);
            caption = 'Успешки';
            rows.push({ key: 'plan', label: 'Сумма планов', value: plan > 0 ? RU_1.format(Math.round(plan * 10) / 10) : '—' });
            rows.push({
                key: 'closure',
                label: 'Выполнение',
                value: percent == null ? '—' : `${RU_0.format(Math.round(percent))} %`,
                valueClassName: hoursPhonePlanTone(percent),
            });
            rows.push({
                key: 'per-hour',
                label: 'Успешки в час',
                value: finite(f.sumDisplayedTotal) > 0 ? formatHoursPhoneNumber(finite(f.sumTezSuccesses) / finite(f.sumDisplayedTotal)) : '—',
            });
            break;
        }
        case 'efficiency':
            value = formatHoursPhoneHours(f.sumEff);
            caption = 'Эффективность';
            rows.push({ key: 'occ', label: 'OCC', value: f.avgOcc == null ? '—' : formatHoursPhonePercent(f.avgOcc) });
            break;
        case 'trainings':
            value = formatHoursPhoneHours(f.trainingsTotal);
            caption = 'Тренинги';
            break;
        case 'technical_issues':
            value = formatHoursPhoneHours(f.technicalIssuesTotal);
            caption = 'Тех. сбои';
            break;
        case 'offline_activity':
            value = formatHoursPhoneHours(f.offlineActivitiesTotal);
            caption = 'Офлайн активность';
            break;
        case 'no_phone':
            value = formatHoursPhoneHours(f.noPhoneTotal);
            caption = 'Без телефона';
            break;
        case 'bonuses':
            value = formatHoursPhoneMoney(f.sumBonuses);
            caption = 'Бонусы';
            break;
        case 'fines':
            value = formatHoursPhoneMoney(f.sumFines);
            caption = 'Штрафы';
            break;
        default:
            break;
    }

    /* Ставка и норма стоят в подвале при любой вкладке — это две колонки,
       которые на компьютере видны всегда, слева от дней. */
    rows.push({ key: 'fte', label: 'Общее FTE', value: f.hasRateRows ? `${RU_2.format(finite(f.sumRate))} FTE` : '—' });
    rows.push({ key: 'norm', label: 'Норма часов', value: formatHoursPhoneHours(f.sumNorm) });

    return { value, caption, rows };
};

/*
 * Месяц оператора ячейками. Значение дня — то же, что в клетке настольной
 * таблицы; заливка — та же шкала, разбитая на ступени.
 */
export const buildHoursPhoneMonth = ({
    op,
    month = '',
    days = [],
    tab = 'work_time',
    monthRelation = 0,
    todayDay = null,
    trainingsByDay = null,
    technicalByDay = null,
    offlineByDay = null,
    successesByDay = null,
    selectedGroupId = null,
    scales = {},
    helpers = defaultHelpers,
}) => {
    const parsed = parseMonth(month);
    const daysOfMonth = asArray(days).length
        ? asArray(days)
        : Array.from({ length: parsed ? new Date(parsed.year, parsed.monthIndex + 1, 0).getDate() : 0 }, (_, index) => index + 1);
    /* Неделя начинается с понедельника: getDay() считает от воскресенья. */
    const leading = parsed ? ((new Date(parsed.year, parsed.monthIndex, 1).getDay() + 6) % 7) : 0;
    const segments = asArray(op?.group_segments);
    const markersSeen = new Set();

    /* Первый проход — числа. Заливку назначаем вторым проходом: у показателей
       без общей шкалы (звонки, чаты, перерыв) масштаб берётся по самому месяцу,
       и до конца обхода он неизвестен. */
    const raw = daysOfMonth.map((day) => {
        const entry = dayEntry(op, day);
        const trainings = asArray(isMap(trainingsByDay) ? trainingsByDay[day] : null);
        const technical = asArray(isMap(technicalByDay) ? technicalByDay[day] : null);
        const offline = asArray(isMap(offlineByDay) ? offlineByDay[day] : null);
        const fines = dayFines(entry);
        const bonuses = dayBonuses(entry);
        const isPast = monthRelation < 0 || (monthRelation === 0 && todayDay != null && day < todayDay);
        const isFuture = monthRelation > 0 || (monthRelation === 0 && todayDay != null && day > todayDay);
        const isToday = monthRelation === 0 && todayDay != null && day === todayDay;

        const segment = segments.find((item) => day >= item.start_day && day <= item.end_day) || null;
        /* Чужой день переведённого оператора: на компьютере это замок, нажатие
           переключает группу. Метки и число у него не наши — не показываем. */
        const locked = Boolean(selectedGroupId && segment && !segment.is_current);

        const markers = [];
        if (!locked) {
            if (trainings.length) markers.push('training');
            if (technical.length) markers.push('technical');
            if (offline.length) markers.push('offline');
            if (fines.length) markers.push('fines');
        }

        const base = {
            day,
            date: hoursPhoneDateStr(month, day),
            markers: tab === 'work_time' ? markers : [],
            locked,
            lockedGroup: locked ? (segment?.group_name || '') : '',
            isToday,
            isFuture,
            isPast,
        };
        if (locked) return { ...base, value: 0, text: '', hue: null, tone: HOURS_PHONE_LOCKED_TONE, scale: null };
        for (const marker of markers) markersSeen.add(marker);

        switch (tab) {
            case 'work_time': {
                const counted = finite(helpers.trainingHours(trainings, (item) => item && item.count_in_hours !== false));
                const value = finite(entry?.work_time) + counted + sumTechnical(technical, helpers) + sumOffline(offline, helpers);
                return { ...base, value, text: value > 0 ? formatHoursPhoneNumber(value) : '', hue: 'green', scale: scales.work };
            }
            case 'break_time': {
                const value = finite(entry?.break_time);
                return { ...base, value, text: value > 0 ? formatHoursPhoneNumber(value) : '', hue: 'blue', scale: null };
            }
            case 'trainings': {
                const value = finite(helpers.trainingHours(trainings));
                const notCounted = finite(helpers.trainingHours(trainings, (item) => item && item.count_in_hours === false));
                return {
                    ...base,
                    value,
                    text: value > 0 ? formatHoursPhoneNumber(value) : '',
                    /* Янтарь — день, где есть незасчитанные часы: на компьютере
                       они стоят отдельной жёлтой плашкой рядом с зелёной. */
                    hue: notCounted > 0 ? 'amber' : 'green',
                    scale: scales.trainings,
                };
            }
            case 'technical_issues': {
                const value = sumTechnical(technical, helpers);
                return { ...base, value, text: value > 0 ? formatHoursPhoneNumber(value) : '', hue: 'violet', scale: scales.technical };
            }
            case 'offline_activity': {
                const value = sumOffline(offline, helpers);
                return { ...base, value, text: value > 0 ? formatHoursPhoneNumber(value) : '', hue: 'green', scale: scales.offline };
            }
            case 'no_phone': {
                const value = finite(helpers.noPhoneHours(entry));
                return { ...base, value, text: value > 0 ? formatHoursPhoneNumber(value) : '', hue: 'rose', scale: scales.noPhone };
            }
            case 'fines': {
                const value = fines.reduce((acc, item) => acc + finite(item.amount ?? item.fine_amount), 0);
                return { ...base, value, text: formatHoursPhoneCompactMoney(value), hue: 'amber', scale: MONEY_SCALE };
            }
            case 'bonuses': {
                const value = bonuses.reduce((acc, item) => acc + finite(item.amount), 0);
                return { ...base, value, text: formatHoursPhoneCompactMoney(value), hue: 'green', scale: MONEY_SCALE };
            }
            case 'efficiency': {
                const work = finite(entry?.work_time);
                const value = finite(entry?.efficiency);
                if (work <= 0) return { ...base, value: 0, text: '', hue: null, scale: null };
                const percent = Math.max(0, Math.min(100, (value / work) * 100));
                return { ...base, value, text: `${RU_0.format(Math.round(percent))}%`, tone: hoursPhoneOccTone(percent), hue: null, scale: null };
            }
            case 'avg_score': {
                const value = finite(entry?.chat_metrics?.avg_score);
                return { ...base, value, text: value > 0 ? formatHoursPhoneNumber(value) : '', hue: 'green', scale: 5 };
            }
            case 'response_time': {
                const value = finite(entry?.chat_metrics?.avg_response_time_seconds);
                if (value <= 0) return { ...base, value: 0, text: '', hue: null, scale: null };
                /* Время ответа — «чем меньше, тем лучше»: общая зелёная шкала
                   красила бы самый тёмный день как лучший. Пороги — секунды. */
                const tone = value <= 60 ? TONE_STEPS.green[2] : value <= 180 ? TONE_STEPS.amber[2] : TONE_STEPS.rose[2];
                return { ...base, value, text: RU_0.format(Math.round(value)), tone, hue: null, scale: null };
            }
            case 'tez_successes': {
                const value = finite(isMap(successesByDay) ? successesByDay[String(day)] : 0);
                return { ...base, value, text: value > 0 ? formatHoursPhoneCount(value) : '', tone: value > 0 ? 'bg-emerald-100 text-emerald-800' : undefined, hue: null, scale: null };
            }
            case 'calls':
            case 'chats': {
                const value = finite(entry?.[tab]);
                return { ...base, value, text: value > 0 ? formatHoursPhoneCount(value) : '', hue: 'blue', scale: null };
            }
            default: {
                const value = finite(entry?.[tab]);
                return { ...base, value, text: value > 0 ? formatHoursPhoneNumber(value) : '', hue: 'blue', scale: null };
            }
        }
    });

    const monthMax = raw.reduce((max, cell) => (cell.value > max ? cell.value : max), 0);

    const cells = raw.map((cell) => {
        if (cell.tone) return cell;
        if (!cell.text) {
            return { ...cell, tone: cell.isFuture ? HOURS_PHONE_FUTURE_TONE : HOURS_PHONE_EMPTY_TONE };
        }
        const scale = finite(cell.scale) > 0 ? finite(cell.scale) : monthMax;
        return { ...cell, tone: hoursPhoneTone(cell.value, scale, cell.hue || 'green') };
    });

    return {
        leading,
        cells,
        markers: MARKER_ORDER.filter((marker) => markersSeen.has(marker)),
    };
};

/* ── Экран дня ───────────────────────────────────────────────────────────── */

export const HOURS_PHONE_FINE_REASONS = ['Корп такси', 'Опоздание', 'Прокси карта', 'Не выход', 'Другое'];
export const HOURS_PHONE_BONUS_TYPES = ['Приведи друга', 'Обучение', 'Съемки'];
const AUTO_FINE_REASONS = new Set(['Опоздание', 'Не выход', 'Прокси карта']);

/* Сумма штрафа: минуты × 50 за опоздание, фиксированные суммы за невыход и
   прокси-карту — те же правила, что в настольном окне дня и в saveCell. */
export const hoursPhoneFineAmount = (fine) => {
    const reason = String(fine?.reason ?? '');
    if (reason === 'Опоздание') return finite(fine?.minutes) * 50;
    if (reason === 'Не выход') return 10000;
    if (reason === 'Прокси карта') return 5000;
    return finite(fine?.amount);
};

export const hoursPhoneFineIsAuto = (fine) => AUTO_FINE_REASONS.has(String(fine?.reason ?? ''));

export const hoursPhoneFineHint = (fine) => {
    const reason = String(fine?.reason ?? '');
    if (reason === 'Опоздание') return 'Сумма считается сама: минуты × 50.';
    if (reason === 'Не выход') return 'Фиксированная сумма: 10 000 ₸.';
    if (reason === 'Прокси карта') return 'Фиксированная сумма: 5 000 ₸.';
    return '';
};

/* Подпись поля количества у бонуса: «Обучение» меряется часами, остальные — штуками. */
export const hoursPhoneBonusQuantityLabel = (type) => (String(type || '').trim() === 'Обучение' ? 'Часы' : 'Кол-во');
export const hoursPhoneBonusHasQuantity = (type) => {
    const value = String(type || '').trim();
    return value === 'Приведи друга' || value === 'Съемки' || value === 'Обучение';
};

/* Поля показателей дня: набор зависит от модели расчёта, как в настольном окне. */
export const buildHoursPhoneDayFields = ({ isChatModel = false }) => {
    const fields = [
        { key: 'work_time', label: 'Отработано', unit: 'ч', step: '0.01' },
        { key: 'break_time', label: 'Перерыв', unit: 'ч', step: '0.01' },
    ];
    if (!isChatModel) fields.push({ key: 'talk_time', label: 'В разговоре', unit: 'ч', step: '0.01' });
    fields.push({ key: 'calls', label: isChatModel ? 'Чаты' : 'Звонки', unit: '', step: '1', integer: true });
    if (!isChatModel) fields.push({ key: 'efficiency', label: 'Эффективность', unit: 'ч', step: '0.01' });
    return fields;
};

export const HOURS_PHONE_CHAT_FIELDS = [
    { key: 'avg_response_time_seconds', label: 'Время ответа', unit: 'с', step: '0.01' },
    { key: 'transfer_chat_count', label: 'Переводы чатов', unit: '', step: '1' },
];

export const hoursPhoneItemTime = (item) => `${item?.start_time || '—'} — ${item?.end_time || '—'}`;

/* Подпись отбора направлений в строке списка. */
export const hoursPhoneDirectionsLabel = (selected = []) => {
    const list = asArray(selected);
    if (!list.length) return 'Ничего';
    if (list.includes('all')) return 'Все';
    if (list.length === 1) return String(list[0]);
    return `Выбрано ${list.length}`;
};
