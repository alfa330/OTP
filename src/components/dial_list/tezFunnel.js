/*
 * «Воронка ОП» — разрез вкладки «Аналитика» раздела «Воронка ОП Tez» (задача #407 Алчинбаевой Анель,
 * 03.11.2026): чистая логика без React. Node-тест — tests/dial_list_tez_funnel.test.mjs.
 *
 * Считает сервер (dial_list/analytics.tez_funnel) по базе успешек — той же, что во вкладке «Успешки»
 * (владелец 06.11.2026): база → уже зарегистрированы → состоялся диалог → зарегистрировались после
 * контакта → вышли к заказам, по городам, и причины отказа. Звонки — Binotel по правилам «Обзвонено» и
 * «Дозвонились» вкладки «Успешки». Здесь — подписи, пояснения под «i», запрос с фильтрами и варианты
 * выбора, в том числе месяцы базы успешек.
 *
 * Даты регистрации и поездок — от TEZ APP (поля с 04.11.2026). По водителю, о котором TEZ APP ещё не
 * ответил, их нет: сколько таких, сервер присылает в tez_unchecked, и экран об этом предупреждает.
 */

import { monthLabel } from './dialListPeriods.js';

export const CITY_NONE = '__none__';

const NF = new Intl.NumberFormat('ru-RU');
const num = (value) => NF.format(Number(value) || 0);
const pctText = (value) => `${Number(value || 0).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}%`;

/** Запрос воронки: пустые фильтры не уходят, месяц базы — 'YYYY-MM'. */
export const funnelQuery = ({ period = '', batchId = '', dateFrom = '', dateTo = '', city = '' } = {}) => {
    const qs = new URLSearchParams();
    if (period) qs.set('period', String(period).slice(0, 7));
    if (batchId) qs.set('batch_id', batchId);
    if (dateFrom) qs.set('date_from', dateFrom);
    if (dateTo) qs.set('date_to', dateTo);
    if (city) qs.set('city', city);
    const text = qs.toString();
    return text ? `?${text}` : '';
};

const dayMonth = (isoDate) => {
    const [, m, d] = String(isoDate || '').split('-');
    return m && d ? `${d.slice(0, 2)}.${m}` : '';
};

/** Окно звонков месяца базы ('24.09–31.10') — как у «Обзвонено» и «Дозвонились» во вкладке «Успешки». */
export const callWindowText = (data) => {
    const from = dayMonth(data?.call_window?.from);
    const to = dayMonth(data?.call_window?.to);
    return from && to ? `${from}–${to}` : '';
};

/** Пояснение к выбору дней звонков: что без выбора и на что дни влияют. */
export const periodHint = (data) => {
    const window = callWindowText(data);
    return `Дни звонков (по Алматы). Без выбора — окно месяца базы${window ? ` ${window}` : ''}, как у «Дозвонились» `
        + 'во вкладке «Успешки». Регистрация и выход к заказам — после первого диалога в эти дни, причины отказа — '
        + 'по отказам этих дней. База и «Уже зарегистрированы» от дней не зависят.';
};

/** Пять шагов воронки — плитки. */
export const funnelSteps = (data) => {
    const t = data?.totals || {};
    return [
        {
            key: 'base', label: 'База', value: num(t.base),
            help: 'Водители базы успешек выбранного месяца — те же, что «Загружено лидов» во вкладке «Успешки», и, если выбран, файла загрузки. Номера «Реестра тестовых номеров» не считаются. Дни звонков на базу не влияют.',
        },
        {
            key: 'already_registered', label: 'Уже зарегистрированы', value: num(t.already_registered),
            hint: 'до первого звонка',
            help: 'Водители базы, у которых аккаунт в TEZ APP создан раньше первого звонка оператора ОП им (недозвон — тоже звонок). Кому ещё не звонили — у кого аккаунт уже есть. Дни звонков на этот шаг не влияют.',
        },
        {
            key: 'dialog', label: 'Состоялся диалог', value: num(t.dialog), hint: 'разговор от 10 с',
            help: 'Водители, с которыми оператор ОП поговорил по исходящему звонку не меньше 10 секунд, — то же, что «Дозвонились» во вкладке «Успешки». Без выбранных дней — в окне месяца базы. Недозвоны и звонки других отделов не считаются. Каждый водитель — один раз.',
        },
        {
            key: 'registered_after', label: 'Зарегистрировались после контакта',
            value: num(t.registered_after), hint: 'после диалога',
            help: 'Водители с диалогом, у которых аккаунт в TEZ APP создан позже их первого диалога. Регистрации до контакта не считаются.',
        },
        {
            key: 'orders_after', label: 'Вышли к заказам', value: num(t.orders_after), hint: 'после диалога',
            tone: Number(t.orders_after) > 0 ? 'good' : null,
            help: 'Водители с диалогом, у которых в TEZ APP есть завершённая поездка, начатая позже их первого диалога: первый заказ месяца базы, заказ в месяце доработки или последняя поездка. Поездки до контакта не считаются; учитываются и ранее зарегистрированные, и новые.',
        },
    ];
};

/** Две конверсии: формула — в подписи, ноль диалогов — 0%. */
export const funnelConversions = (data) => {
    const r = data?.rates || {};
    return [
        {
            key: 'dialog_to_registration', label: 'Из диалога в регистрацию', value: pctText(r.dialog_to_registration),
            hint: 'зарегистрировались после ÷ диалог',
            help: 'Зарегистрировались после контакта ÷ состоялся диалог × 100%. Нет диалогов — 0%.',
        },
        {
            key: 'dialog_to_orders', label: 'Из диалога в выход к заказам', value: pctText(r.dialog_to_orders),
            hint: 'вышли к заказам ÷ диалог',
            help: 'Вышли к заказам ÷ состоялся диалог × 100%. Нет диалогов — 0%.',
        },
    ];
};

/**
 * Водители базы, о которых TEZ APP ещё не ответил: регистрации и поездок у них нет, и шаги с ними
 * меньше настоящих — об этом надо сказать, а не показывать молча. '' — ответил обо всех.
 */
export const uncheckedNotice = (data) => {
    const unchecked = Number(data?.tez_unchecked) || 0;
    if (unchecked <= 0) return '';
    return `TEZ APP ещё не ответил по ${num(unchecked)} из ${num(data?.totals?.base)} водителей базы — `
        + 'их регистрации и поездки в воронке пока не учтены. Их спросит ночная сверка или кнопка '
        + '«Сверить сейчас» во вкладке «Успешки» за этот месяц.';
};

/** Месяцы базы успешек — свежие сверху, с числом водителей; выбранный есть всегда (сервер добавит). */
export const monthOptions = (data) => (Array.isArray(data?.options?.months) ? data.options.months : [])
    .map((m) => ({ value: String(m.period), label: monthLabel(m.period), meta: num(m.base) }));

export const cityLabel = (name) => (String(name || '').trim() ? String(name).trim() : 'Город не указан');

/** Варианты города: «Все города», города базы (с числом водителей), «не указан» — последним. */
export const cityOptions = (data) => [
    { value: '', label: 'Все города' },
    ...(Array.isArray(data?.options?.cities) ? data.options.cities : []).map((c) => ({
        value: String(c.key || CITY_NONE), label: cityLabel(c.name), meta: num(c.base),
    })),
];

const fmtDay = (iso) => {
    const d = iso ? new Date(iso) : null;
    if (!d || Number.isNaN(d.getTime())) return '';
    return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}`;
};

/** Варианты файла загрузки месяца: «Все загрузки» и файлы — свежие сверху, с датой загрузки. */
export const batchOptions = (data) => [
    { value: '', label: 'Все загрузки' },
    ...(Array.isArray(data?.options?.batches) ? data.options.batches : []).map((b) => ({
        value: String(b.id), label: b.file_name || 'файл', meta: fmtDay(b.created_at),
    })),
];

export const reasonLabel = (reason) => (String(reason?.name || '').trim() || 'Без причины');

/*
 * Итоги звонков (27.11.2026). Анель: «в воронке не хватает показателей по итогам диалога — согласен,
 * подумает, отказ», «согласия и отказы как основные показатели отдельно»; владелец: «всё, что в
 * настройках итогов звонка добавлено, всё должно быть выведено». Считает сервер: у каждого водителя базы —
 * последний итог в выбранные дни, все итоги настроек по порядку и с нулём, доля — от водителей с итогом.
 */
const HEX_RE = /^#[0-9A-Fa-f]{6}$/;
export const OUTCOME_COLOR_DEFAULT = '#8E8E93';

/** Строки отчёта «Итоги звонков». Типы итога — строками под ним, только если операторы их выбирали: у итога
 *  без типов сервер отдаёт одну запись «без типа» на всё число — строкой она повторяла бы сам итог («Согласен
 *  103 → Без типа 103», стенд 27.11.2026). У отказа типов здесь нет: причины отказа — отдельный отчёт рядом. */
export const outcomeRows = (data) => (Array.isArray(data?.outcomes?.items) ? data.outcomes.items : []).map((item) => {
    const used = (Array.isArray(item.subtypes) ? item.subtypes : []).filter((s) => Number(s.count) > 0);
    const typed = item.kind !== 'refusal' && used.some((s) => s.id);
    return {
        key: String(item.outcome_id || item.name || ''),
        name: String(item.name || '').trim() || 'Без названия',
        color: HEX_RE.test(item.color || '') ? item.color : OUTCOME_COLOR_DEFAULT,
        count: Number(item.count) || 0,
        kind: item.kind || '',
        inactive: item.is_active === false,
        subtypes: typed ? used.map((s) => ({
            key: String(s.id || 'none'), name: String(s.name || '').trim() || 'Без типа', count: Number(s.count) || 0,
        })) : [],
    };
});

/** Водителей с итогом в выбранные дни — знаменатель долей в «Итогах звонков» и у плиток согласий и отказов. */
export const outcomesTotal = (data) => Number(data?.outcomes?.total) || 0;

/** Сервер считает итоги (с 27.11.2026). Сайт и сервер выкладываются порознь: ответ без итогов — не «ноль
 *  согласий», а «ещё не считает» — плиток нет, на месте таблицы — пояснение. */
export const outcomesReady = (data) => Array.isArray(data?.outcomes?.items);

export const OUTCOMES_PENDING_TEXT = 'Итоги звонков появятся после обновления сервера.';

// Целыми процентами — как доли в «Итогах звонков» (полоса reportKit.Bar): одно число на экране — одна запись.
const shareOfOutcomes = (part, total) => pctText(total ? Math.round((100 * part) / total) : 0);

/** Согласия и отказы — главные показатели воронки, отдельными плитками рядом с конверсиями. */
export const outcomeTiles = (data) => {
    if (!outcomesReady(data)) return [];
    const o = data?.outcomes || {};
    const total = outcomesTotal(data);
    const agreements = Number(o.agreements) || 0;
    const refusals = Number(o.refusals) || 0;
    return [
        {
            key: 'agreements', label: 'Согласились', value: num(agreements),
            hint: `${shareOfOutcomes(agreements, total)} от всех итогов`,
            help: 'Водители, у которых последний итог в выбранные дни — согласие: итог с датой выхода на линию или названный «Согласен…». Каждый водитель — один раз. Доля — от всех водителей с итогом (сумма «Итогов звонков»). В разрезе «Автообзвон» «Согласились» считают иначе — всех, кто хоть раз согласился за месяц базы автообзвона, поэтому числа могут не совпадать.',
        },
        {
            key: 'refusals', label: 'Отказались', value: num(refusals),
            hint: `${shareOfOutcomes(refusals, total)} от всех итогов`,
            help: 'Водители, у которых последний итог в выбранные дни — «Отказ». Отказавший, а потом согласившийся — уже не отказ. Причины — в отчёте «Причины отказа», их сумма равна этому числу. Доля — от всех водителей с итогом.',
        },
    ];
};

/*
 * Раскладка (27.11.2026, владелец: «нужно сделать всё симметрично», «чтобы всё влезало в один экран для скриншота
 * и было читабельно», «адаптив под любой экран»). Решает ширина САМОГО блока, а не окна: открытый сайдбар отнимает
 * 220 px, и брейкпоинты окна на одном и том же мониторе дали бы то три колонки, то тесноту.
 *   'three'  — три равные колонки: «Итоги звонков» | «Причины отказа» в две колонки. Карточки одной высоты,
 *              строки «Всего» на одном уровне; 14 причин встают по 7 — вровень с семью итогами;
 *   'stack'  — друг под другом во всю ширину, причины по-прежнему в две колонки;
 *   'single' — телефон: друг под другом, причины одной колонкой.
 */
export const REPORT_THREE_MIN_PX = 1040;
export const REPORT_SPLIT_MIN_PX = 560;
// Полосе доли нужна колонка не уже этого: имя причины, число и полоса — в одну строку (крупный шрифт — от xl).
export const BARS_MIN_COLUMN_PX = { big: 400, base: 330 };
const REPORT_GAP_PX = 16;

export const reportLayout = (width) => {
    const w = Number(width) || 0;
    if (w >= REPORT_THREE_MIN_PX) return 'three';
    if (w >= REPORT_SPLIT_MIN_PX) return 'stack';
    return 'single';
};

/** Ширина самой узкой колонки списка: три колонки — треть без зазоров, друг под другом — половина (причины в две
 *  колонки), телефон — вся ширина. */
export const listColumnWidth = (width) => {
    const w = Number(width) || 0;
    const layout = reportLayout(w);
    if (layout === 'three') return (w - 2 * REPORT_GAP_PX) / 3;
    if (layout === 'stack') return w / 2;
    return w;
};

/** Полосы долей — только если в самой узкой колонке имя, число и полоса встают в одну строку; иначе — процент без
 *  полосы: перенос имени сделал бы строки разной высоты и сломал ровный ряд. big — крупный шрифт (окно от 1280). */
export const reportShowsBars = (width, big = false) =>
    listColumnWidth(width) >= (big ? BARS_MIN_COLUMN_PX.big : BARS_MIN_COLUMN_PX.base);

/** Фильтры одной строкой (подпись слева от поля) — только на крупном шрифте и когда раздел не уже этого: иначе
 *  «Ноябрь 2026» и «Весь период» обрезаются до «Ноябрь…» и «Ве…» (окно 1440 с открытым сайдбаром). */
export const FILTERS_INLINE_MIN_PX = 1200;
export const filtersInline = (width, big = false) => !!big && (Number(width) || 0) >= FILTERS_INLINE_MIN_PX;

/** Две колонки по порядку: сверху вниз в первой, потом во второй; в первой — половина с округлением вверх. */
export const splitInTwo = (list) => {
    const items = Array.isArray(list) ? list : [];
    const half = Math.ceil(items.length / 2);
    return [items.slice(0, half), items.slice(half)];
};

/** Плитка шага воронки в ряду: края рядов совпадают на любой ширине. Телефон (2 колонки) — нечётная последняя во
 *  всю ширину (5 → 2+2+1); планшет (сетка на 6) — 5 шагов по 3+2, 4 — по 2+2; компьютер — все в ряд. */
export const stepTileClass = (index, count) => {
    const classes = [];
    if (count % 2 === 1 && index === count - 1) classes.push('col-span-2');
    if (count === 5) classes.push(index < 3 ? 'sm:col-span-2' : 'sm:col-span-3');
    else classes.push('sm:col-span-3');
    classes.push('lg:col-span-1');
    return classes.join(' ');
};

/** Итоги есть, а согласия среди них нет (переименовали и сняли дату) — плитка «Согласились» молча
 *  показывала бы ноль. '' — согласие настроено или итогов нет вовсе. */
export const agreementSetupNotice = (data) => {
    const items = Array.isArray(data?.outcomes?.items) ? data.outcomes.items : null;
    if (!items || !items.length || items.some((i) => i.kind === 'agreement')) return '';
    return 'Среди итогов отдела нет согласия — плитка «Согласились» считает итог с датой выхода на линию или '
        + 'названный «Согласен…». Проверьте итоги во вкладке «Настройки» → «Итоги».';
};

/**
 * Что сказать над отчётом о причинах, если его не из чего строить: итога «Отказ» нет вовсе или у него
 * не заведены причины (тогда телефон их не спрашивает). '' — всё настроено.
 */
export const refusalSetupNotice = (data) => {
    const outcomes = Array.isArray(data?.refusals?.outcomes) ? data.refusals.outcomes : null;
    if (!outcomes) return '';
    if (!outcomes.length) return 'В итогах отдела нет итога «Отказ» — отчёт о причинах строится по нему. Добавьте его во вкладке «Настройки» → «Итоги».';
    if (outcomes.every((o) => !Number(o.reasons))) {
        return 'У итога «Отказ» нет причин. Заведите их во вкладке «Настройки» → «Итоги» (типы итога) — тогда телефон будет спрашивать причину при каждом отказе.';
    }
    return '';
};
