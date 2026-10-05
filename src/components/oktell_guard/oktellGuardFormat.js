/*
 * Форматирование раздела «Ограничитель Перезвона» — общее для обеих его частей:
 * СЗоВ (агент Oktell, OktellGuardView.jsx) и отдела продаж (автоофлайн iCORE
 * Phone, OktellGuardPhonePanel.jsx). Вынесено из OktellGuardView, когда частей
 * стало две: вторая копия parseServerTime разъехалась бы с первой молча, а
 * ошибка в ней не падает, а сдвигает время на пять часов.
 *
 * Модуль чистый (ни React, ни сети) — его гоняет tests/oktell_guard_phone.test.mjs.
 */

export const fmtMinutes = (seconds) => {
    const value = Number(seconds || 0);
    if (!value) return '—';
    if (value % 60 === 0) return `${value / 60} мин`;
    return `${Math.floor(value / 60)} мин ${value % 60} с`;
};

/**
 * Время из базы приходит МЕСТНЫМ (Алматы), а Flask отдаёт его строкой с
 * пометкой GMT. Браузер читает такую строку как UTC и уводит момент на +5 часов
 * вперёд. Последствия были не косметические: «Молчит с …» не загоралось вообще
 * никогда (возраст отметки получался отрицательным, порог 15 минут не
 * срабатывал), то есть намертво замолчавший агент выглядел живым.
 */
export const parseServerTime = (raw) => {
    if (!raw) return null;
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return null;
    if (!/GMT|UTC|Z$|\+00:?00$/i.test(String(raw))) return parsed;
    // Возвращаем момент туда, где он был записан: пометку GMT поставил
    // сериализатор, а не база.
    return new Date(parsed.getTime() + parsed.getTimezoneOffset() * 60000);
};

export const fmtDateTime = (raw) => {
    if (!raw) return '';
    const parsed = parseServerTime(raw);
    if (!parsed) return String(raw).slice(0, 16).replace('T', ' ');
    return parsed.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

/** Только часы и минуты — для строки отчёта, где дата уже стоит в своей колонке. */
export const fmtTime = (raw) => {
    if (!raw) return '';
    const parsed = parseServerTime(raw);
    if (!parsed) return String(raw).slice(11, 16);
    return parsed.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

export const fmtDay = (raw) => {
    if (!raw) return '';
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return String(raw).slice(0, 10);
    return parsed.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long' });
};

export const fmtSize = (bytes) => {
    const value = Number(bytes || 0);
    if (!value) return '';
    return `${(value / (1024 * 1024)).toFixed(1)} МБ`;
};

/**
 * Дата «N дней назад» для полей отчёта — по МЕСТНОМУ календарю. Раньше её
 * резали из toISOString(), а это дата по UTC: в Алматы (UTC+5) с полуночи до
 * пяти утра «сегодня» получалось вчерашним, и ночные выбросы в отчёт по
 * умолчанию не попадали. `now` — только для теста.
 */
export const isoDaysAgo = (days, now = new Date()) => {
    const d = new Date(now.getTime());
    d.setDate(d.getDate() - days);
    const pad = (value) => String(value).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};
