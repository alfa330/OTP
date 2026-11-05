/*
 * «Воронка ОП» — разрез вкладки «Аналитика» раздела «Воронка ОП Tez» (задача #407 Алчинбаевой Анель,
 * 03.11.2026): чистая логика без React. Node-тест — tests/dial_list_tez_funnel.test.mjs.
 *
 * Считает сервер (dial_list/analytics.tez_funnel): база → уже зарегистрированы → состоялся диалог →
 * зарегистрировались после контакта → вышли к заказам, по городам, и причины отказа. Здесь — подписи,
 * пояснения под «i», запрос с фильтрами и варианты выбора.
 *
 * Даты регистрации и поездок — от TEZ APP (поля с 04.11.2026). По водителю, о котором TEZ APP ещё не
 * ответил, их нет: сколько таких, сервер присылает в tez_unchecked, и экран об этом предупреждает.
 */

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

/** Пять шагов воронки — плитки. */
export const funnelSteps = (data) => {
    const t = data?.totals || {};
    return [
        {
            key: 'base', label: 'База', value: num(t.base),
            help: 'Водители выбранной базы — месяц и, если выбран, файл загрузки. Номера «Реестра тестовых номеров» не считаются. Период звонков на базу не влияет.',
        },
        {
            key: 'already_registered', label: 'Уже зарегистрированы', value: num(t.already_registered),
            hint: 'до первого звонка',
            help: 'Водители базы, у которых аккаунт в TEZ APP создан раньше первого звонка им (недозвон — тоже звонок). Кому ещё не звонили — у кого аккаунт уже есть. Период звонков на этот шаг не влияет.',
        },
        {
            key: 'dialog', label: 'Состоялся диалог', value: num(t.dialog), hint: 'разговор от 10 с',
            help: 'Водители, с которыми оператор поговорил не меньше 10 секунд в выбранный период звонков. Недозвоны, сброс, автоответчик и сообщение сети не считаются. Каждый водитель — один раз.',
        },
        {
            key: 'registered_after', label: 'Зарегистрировались после контакта',
            value: num(t.registered_after), hint: 'после диалога',
            help: 'Водители с диалогом, у которых аккаунт в TEZ APP создан позже их первого диалога в выбранный период. Регистрации до контакта не считаются.',
        },
        {
            key: 'orders_after', label: 'Вышли к заказам', value: num(t.orders_after), hint: 'после диалога',
            tone: Number(t.orders_after) > 0 ? 'good' : null,
            help: 'Водители с диалогом, у которых в TEZ APP есть завершённая поездка, начатая позже их первого диалога в выбранный период. Поездки следующего месяца учитываются до его 7-го числа — пока базу проверяют. Поездки до контакта не считаются; учитываются и ранее зарегистрированные, и новые.',
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
        + 'их регистрации и поездки в воронке пока не учтены.';
};

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
