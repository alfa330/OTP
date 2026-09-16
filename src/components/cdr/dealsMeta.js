/* Подписи и форматы режима «Сделки» раздела «Касания».
 *
 * Отдельный модуль по той же причине, что touchMeta.js: часть правил обязана
 * совпадать с сервером (имя файла — с cdr/lead_report.py:report_filename), и
 * совпадение проверяется тестом tests/cdr_deals_meta.test.mjs.
 */

import { fullDay } from './touchMeta.js';

/** Источники записей: ключ → как он называется человеку. Порядок — как в меню. */
export const SOURCE_OPTIONS = [
    { value: 'amo', label: 'Основа' },
    { value: 'crm_paid_hire', label: 'Платный найм' },
    { value: 'crm_stream', label: 'Поток' },
];

export const SOURCE_LABELS = Object.fromEntries(SOURCE_OPTIONS.map((o) => [o.value, o.label]));

/** Кто в строке: сделка, водитель или лид — подписи меняются вместе с источником. */
export const SUBJECT = {
    amo: { one: 'сделка', many: 'сделок', gen: 'сделки', date: 'Создана' },
    crm_paid_hire: { one: 'водитель', many: 'водителей', gen: 'водителя', date: 'Зарегистрирован' },
    crm_stream: { one: 'лид', many: 'лидов', gen: 'лида', date: 'Взят в работу' },
};

export const subjectOf = (source) => SUBJECT[source] || SUBJECT.amo;

export const PRESENCE_OPTIONS = [
    { value: 'all', label: 'Все' },
    { value: 'with', label: 'Со звонками' },
    { value: 'without', label: 'Без звонков' },
];

/** Имя файла выгрузки. Совпадает с серверным report_filename: файл живёт в
 *  «Загрузках» месяцами, и год в имени обязателен. */
export const dealsFileName = (source, from, to, ext = 'xlsx') => {
    const label = SOURCE_LABELS[source] || 'Сделки';
    if (from === to) return `${label} ${fullDay(from)} + касания ОП.${ext}`;
    return `${label} ${fullDay(from)}-${fullDay(to)} + касания ОП.${ext}`;
};

/** «Реакция ОП»: минуты → короткая подпись. Отрицательное — оператор набрал
 *  номер раньше, чем завёл карточку; это нормально и показывается как есть. */
export const reactionLabel = (minutes) => {
    if (minutes == null || minutes === '') return '—';
    const value = Number(minutes);
    if (!Number.isFinite(value)) return '—';
    const abs = Math.abs(value);
    const sign = value < 0 ? '−' : '';
    if (abs < 1) return `${sign}${Math.round(abs * 60)} с`;
    if (abs < 60) return `${sign}${Math.round(abs)} мин`;
    if (abs < 60 * 24) return `${sign}${Math.round((abs / 60) * 10) / 10} ч`;
    return `${sign}${Math.round((abs / 1440) * 10) / 10} сут`;
};

/** Сколько активных фильтров показывать на кнопке «Фильтры · N». Период,
 *  источник и страница фильтрами не считаются — они всегда заданы. */
export const countActiveFilters = (filters) => [
    filters.park, filters.city, filters.stage, filters.leadType, filters.owner,
    filters.presence && filters.presence !== 'all', filters.talkedOnly, filters.ownOnly,
    filters.noWindow, filters.phone,
].filter(Boolean).length;

/** Параметры запроса из состояния фильтров: только заданные, без пустых. */
export const dealsQuery = (source, range, filters, extra = {}) => {
    const params = { source, date_from: range.from, date_to: range.to, ...extra };
    if (filters.touchesTo) params.touches_to = filters.touchesTo;
    if (filters.park) params.park = filters.park;
    if (filters.city) params.city = filters.city;
    if (filters.stage) params.stage = filters.stage;
    if (filters.leadType) params.lead_type = filters.leadType;
    if (filters.owner) params.owner = filters.owner;
    if (filters.phone) params.phone = filters.phone;
    if (filters.presence && filters.presence !== 'all') params.presence = filters.presence;
    if (filters.talkedOnly) params.talked_only = 1;
    if (filters.ownOnly) params.own_only = 1;
    if (filters.noWindow) params.no_window = 1;
    return params;
};
