// Форматирование «Профиля»: даты и стаж словами, тон показателей.
//
// Модуль без React — его грузит напрямую Node в tests/profile_view.test.mjs.

const MONTHS_GENITIVE = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

// 1 год, 2 года, 5 лет; 1 месяц, 3 месяца, 7 месяцев.
export const pluralRu = (count, one, few, many) => {
    const n = Math.abs(Number(count)) % 100;
    const last = n % 10;
    if (n > 10 && n < 20) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

const parseIsoDate = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? ''));
    if (!match) return null;
    const [, y, m, d] = match.map(Number);
    if (m < 1 || m > 12 || d < 1 || d > 31) return null;
    return { y, m, d };
};

// «2025-07-20» → «20 июля 2025». Непонятное значение — как есть.
export const formatHireDate = (value) => {
    const date = parseIsoDate(value);
    if (!date) return String(value ?? '').trim();
    return `${date.d} ${MONTHS_GENITIVE[date.m - 1]} ${date.y}`;
};

// Стаж по календарным месяцам: «1 год 2 месяца», «5 месяцев», «Меньше месяца».
// Счёт тот же, что был в плитке «Стаж работы» (разница месяцев без учёта дня).
export const formatTenure = (hireDate, today = new Date()) => {
    const date = parseIsoDate(hireDate);
    if (!date) return '';
    const months = (today.getFullYear() - date.y) * 12 + (today.getMonth() + 1 - date.m);
    if (months < 1) return 'Меньше месяца';
    const years = Math.floor(months / 12);
    const rest = months % 12;
    const parts = [];
    if (years) parts.push(`${years} ${pluralRu(years, 'год', 'года', 'лет')}`);
    if (rest) parts.push(`${rest} ${pluralRu(rest, 'месяц', 'месяца', 'месяцев')}`);
    return parts.join(' ');
};

// Цвет числа — только когда он что-то значит (порог достигнут или нет).
export const TONE_CLASS = {
    good: 'text-emerald-600',
    warn: 'text-amber-600',
    bad: 'text-rose-600',
    muted: 'text-slate-400',
};
export const toneClass = (tone, fallback = 'text-slate-900') => TONE_CLASS[tone] || fallback;
