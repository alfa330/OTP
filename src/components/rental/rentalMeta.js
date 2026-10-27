/*
 * Подписи и форматирование раздела «Аренда авто» (ночной отчёт #398).
 *
 * Время с сервера — наивное время Алматы («2026-10-28T01:00:00»), поэтому разбирается
 * по цифрам строки, а не через new Date(): в браузере с другим поясом Date сдвинул бы
 * «01:00» на чужие часы.
 */

export const GPS_LABELS = { online: 'Онлайн', offline: 'Офлайн', none: 'Нет GPS' };
export const RENT_LABELS = { in_rent: 'В аренде', no: 'Нет' };
export const STATE_LABELS = { base: 'На базе', repair: 'На ремонте', sale: 'На продаже' };
export const STATES = ['base', 'repair', 'sale'];

export const SOURCE_NAMES = { yume: 'Yume', wialon: 'Wialon', glonass: 'ГЛОНАСС', efir: 'Efir' };
const GPS_SOURCES = ['wialon', 'glonass', 'efir'];

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
    'сентября', 'октября', 'ноября', 'декабря'];

const parts = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ''));
    if (!match) return null;
    const [, year, month, day, hour, minute] = match;
    return { year: +year, month: +month, day: +day, hour, minute };
};

/** «28 октября, 01:00» — слот отчёта. */
export const slotLabel = (iso) => {
    const p = parts(iso);
    return p ? `${p.day} ${MONTHS[p.month - 1]}, ${p.hour}:${p.minute}` : '';
};

/** Время отчёта рядом с выбором даты: вид слота на сервере → подпись. */
export const KIND_OPTIONS = [{ value: 'night', label: '01:00' }, { value: 'morning', label: '07:00' }];
const KIND_CLOCK = { night: '01:00', morning: '07:00' };

/** «28 октября, 01:00» — выбранные дата (ГГГГ-ММ-ДД) и время отчёта. */
export const pickLabel = (date, kind) => slotLabel(`${date}T${KIND_CLOCK[kind] || '01:00'}:00`);

/** «28.10 01:12» — время сигнала и сбора. */
export const shortTime = (iso) => {
    const p = parts(iso);
    return p ? `${String(p.day).padStart(2, '0')}.${String(p.month).padStart(2, '0')} ${p.hour}:${p.minute}` : '';
};

/** «01:12» — время сбора отчёта той же ночи. */
export const clockTime = (iso) => {
    const p = parts(iso);
    return p ? `${p.hour}:${p.minute}` : '';
};

const toMinutes = (iso) => {
    const p = parts(iso);
    return p ? Date.UTC(p.year, p.month - 1, p.day, +p.hour, +p.minute) / 60000 : null;
};

/** Сколько машина молчит к моменту сбора: «6 ч», «2 дн». Пусто, если меньше часа. */
export const silenceLabel = (signalIso, collectedIso) => {
    const from = toMinutes(signalIso);
    const to = toMinutes(collectedIso);
    if (from === null || to === null || to <= from) return '';
    const hours = Math.floor((to - from) / 60);
    if (hours < 1) return '';
    if (hours < 48) return `${hours} ч`;
    return `${Math.floor(hours / 24)} дн`;
};

/** Госномер для поиска: латиница, без пробелов; кириллические двойники → латиница. */
const CYR = { А: 'A', В: 'B', Е: 'E', К: 'K', М: 'M', Н: 'H', О: 'O', Р: 'P', С: 'C', Т: 'T', У: 'Y', Х: 'X' };
export const normalizePlate = (text) => String(text || '').toUpperCase()
    .replace(/[АВЕКМНОРСТУХ]/g, (ch) => CYR[ch])
    .replace(/[^A-Z0-9]/g, '');

export const FILTERS = [
    { value: 'all', label: 'Все', test: () => true },
    { value: 'offline', label: 'Офлайн', test: (row) => row.gps_status === 'offline' },
    { value: 'none', label: 'Без GPS', test: (row) => row.gps_status === 'none' },
    { value: 'check', label: 'Уточнить', test: (row) => row.needs_check },
];

export const matchesQuery = (row, query) => {
    const text = String(query || '').trim();
    if (!text) return true;
    const plate = normalizePlate(text);
    if (plate && normalizePlate(row.plate).includes(plate)) return true;
    const lower = text.toLowerCase();
    return [row.address, row.gps_site, row.model].some((value) => String(value || '').toLowerCase().includes(lower));
};

/** Кто из источников не ответил — для плашки над отчётом. */
export const failedSources = (sources) => Object.entries(sources || {})
    .filter(([, status]) => status && status.ok === false)
    .map(([name, status]) => ({ name, label: SOURCE_NAMES[name] || name, error: status.error || '' }));

export const gpsSourceFailed = (sources) => GPS_SOURCES.some((name) => sources?.[name] && sources[name].ok === false);

export const errorOf = (error, fallback) => error?.response?.data?.error || fallback;

/** «Аренда авто 28.10.2026 01-00.xlsx» — как имя файла с сервера. */
export const exportFileName = (report) => {
    const p = parts(report?.slot_at);
    if (!p) return 'Аренда авто.xlsx';
    const dd = String(p.day).padStart(2, '0');
    const mm = String(p.month).padStart(2, '0');
    return `Аренда авто ${dd}.${mm}.${p.year} ${p.hour}-${p.minute}.xlsx`;
};
