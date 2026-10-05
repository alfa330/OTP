/*
 * Отдел продаж в «Ограничителе Перезвона»: автоофлайн iCORE Phone (ТЗ 05.10.2026).
 *
 * У групп ЯР и Поток основной статус — «Исход». Простой в нём (без инициации
 * звонка) копит сам телефон; дошёл до порога — телефон ставит «Офлайн» и шлёт
 * это событием статуса с пометкой «авто» («выброс»). Сервер записывает выброс,
 * раздел его показывает и хранит правило: включено ли, порог, предупреждение,
 * какие группы. У Основы «Исхода» нет, поэтому в правиле её нет вовсе.
 *
 * Здесь только чистая логика панели (без React и сети): её гоняет
 * tests/oktell_guard_phone.test.mjs. Границы чисел повторяют
 * oktell_guard/phone.py — сервер всё равно нормализует сам, а здесь они нужны,
 * чтобы поле не обещало то, что сервер молча срежет.
 */

import { fmtMinutes } from './oktellGuardFormat.js';

export const PHONE_DEPARTMENT_CODE = 'op';

// Пороги списком, как у СЗоВ: это решение про людей, а не поле для опечатки.
// 5 минут — значение из ТЗ, остальное — запас в обе стороны.
export const PHONE_THRESHOLD_PRESETS = [180, 240, 300, 360, 600];

// Как в oktell_guard/phone.py: clamp_idle_threshold / clamp_warn_before.
export const PHONE_THRESHOLD_MIN_S = 60;
export const PHONE_THRESHOLD_MAX_S = 3600;
export const PHONE_WARN_MAX_S = 600;

// Запасные подписи и порядок групп, если сервер их не прислал (старый ответ).
export const PHONE_GROUP_LABELS = { osnova: 'Основа', yar: 'ЯР', potok: 'Поток' };
export const PHONE_IDLE_GROUPS = ['yar', 'potok'];

export const PHONE_GROUP_ALL = 'all';

// Цвет группы — по основному статусу её телефона: зелёный «Активный» у Основы,
// оранжевый «Исход» у ЯР, синий «Автодозвон» у Потока.
const GROUP_TONES = { osnova: 'green', yar: 'amber', potok: 'blue' };

export const phoneGroupTone = (code) => GROUP_TONES[code] || 'slate';

export const phoneGroupLabel = (code, labels) => (
    (labels && labels[code]) || PHONE_GROUP_LABELS[code] || ''
);

/**
 * Верхняя граница «Предупреждать за» при данном пороге. Предупреждение не может
 * начаться раньше, чем через полминуты простоя: иначе плашка горела бы всегда.
 */
export const phoneWarnUpperBound = (thresholdSeconds) => {
    const threshold = Number(thresholdSeconds) || 0;
    return Math.max(0, Math.min(PHONE_WARN_MAX_S, threshold - 30));
};

/** Значение поля «Предупреждать за» → то, что отправим на сервер (целое, в границах). */
export const clampPhoneWarn = (value, thresholdSeconds) => {
    const number = Math.round(Number(value));
    if (!Number.isFinite(number)) return 0;
    return Math.max(0, Math.min(phoneWarnUpperBound(thresholdSeconds), number));
};

/**
 * Что делать, когда поле «Предупреждать за» теряет фокус.
 *
 * Сохранять на каждый уход из поля нельзя: сохранение гасит все контролы
 * правила, а фокус уходит по mousedown следующего — щелчок по порогу или
 * галочке группы пропадал, и «Изменено … · имя» переписывалось на того, кто
 * ничего не менял. Поэтому PUT — только когда значение правда другое.
 *   пустое поле  → вернуть сохранённое (стёртое поле — не «ноль секунд»:
 *                  ноль отключает предупреждение, его вводят цифрой);
 *   то же число  → только привести показ («060» → 60), без запроса;
 *   другое       → сохранить.
 * `saved` — последнее значение, подтверждённое сервером.
 */
export const phoneWarnBlurDecision = (raw, saved, thresholdSeconds) => {
    const savedNumber = Number(saved);
    const current = Number.isFinite(savedNumber) ? savedNumber : 0;
    if (String(raw ?? '').trim() === '') return { value: current, save: false };
    const next = clampPhoneWarn(raw, thresholdSeconds);
    return { value: next, save: next !== current };
};

/**
 * Группы правила после щелчка по галочке — в каноническом порядке idleGroups,
 * без повторов и без чужих кодов. Пустой список — осознанное «ни одной».
 */
export const togglePhoneGroup = (groups, code, idleGroups = PHONE_IDLE_GROUPS) => {
    const current = new Set(Array.isArray(groups) ? groups : []);
    if (current.has(code)) current.delete(code); else current.add(code);
    return idleGroups.filter((group) => current.has(group));
};

/** Цифры шапки. «Под правилом» — по флагу сервера, а не пересчётом здесь. */
export const phoneEmployeeStats = (employees) => {
    const rows = Array.isArray(employees) ? employees : [];
    return {
        total: rows.length,
        participating: rows.filter((row) => row.participates).length,
        kickedPeople: rows.filter((row) => Number(row.kicks_30d || 0) > 0).length,
        kicks: rows.reduce((sum, row) => sum + Number(row.kicks_30d || 0), 0),
    };
};

/** Поиск по имени или SIP-номеру и фильтр по группе. */
export const filterPhoneEmployees = (employees, { search = '', group = PHONE_GROUP_ALL } = {}) => {
    const rows = Array.isArray(employees) ? employees : [];
    const needle = String(search || '').trim().toLowerCase();
    return rows.filter((row) => {
        if (group && group !== PHONE_GROUP_ALL && row.status_group !== group) return false;
        if (!needle) return true;
        return String(row.name || '').toLowerCase().includes(needle)
            || String(row.sip_number || '').includes(needle);
    });
};

/** Варианты фильтра: «Все» и группы, которые реально есть в списке, в порядке групп. */
export const phoneGroupOptions = (employees, labels) => {
    const rows = Array.isArray(employees) ? employees : [];
    const present = new Set(rows.map((row) => row.status_group).filter(Boolean));
    const order = Object.keys(PHONE_GROUP_LABELS);
    return [
        { value: PHONE_GROUP_ALL, label: 'Все' },
        ...order.filter((code) => present.has(code))
            .map((code) => ({ value: code, label: phoneGroupLabel(code, labels) })),
    ];
};

/** Подзаголовок шапки: правило одной строкой. */
export const phoneRuleSummary = (rule, labels) => {
    if (!rule || !rule.enabled) return 'Правило выключено — телефоны никого не переводят в «Офлайн»';
    const groups = Array.isArray(rule.groups) ? rule.groups : [];
    if (!groups.length) return 'Правило включено, но ни одна группа не выбрана — никого не выкидывает';
    const names = groups.map((code) => phoneGroupLabel(code, labels) || code).join(' и ');
    return `${names}: «Офлайн» после ${fmtMinutes(rule.threshold_s)} без звонков в «Исходе»`;
};
