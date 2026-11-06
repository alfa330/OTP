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
