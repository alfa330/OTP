/*
 * «Списки Байги» (#356): чистая логика экрана — фильтры, адрес, подписи, числа.
 *
 * Пары с сервером (размеры страниц, режимы приза, ключи сортировки и
 * диапазонов) сверяет tests/test_baiga.py: разойдись они, экран просил бы то,
 * чего сервер не понимает, и фильтр молча не работал бы.
 */
import { stripTechnicalQueryParams } from '../../utils/urlHygiene.js';

export const BAIGA_VIEW = 'baiga';

export const PAGE_SIZES = [50, 100, 500];
export const DEFAULT_PAGE_SIZE = 50;

export const PRIZE_ANY = 'any';
export const PRIZE_NONE = 'none';

// Сколько ждать после последней буквы поиска, прежде чем идти на сервер.
export const SEARCH_DEBOUNCE_MS = 300;

/* Колонки таблицы: ключ сортировки → подпись. Ключи — те же, что
   filters.SORT_COLUMNS на сервере. */
export const COLUMN_LABELS = {
    week: 'Неделя',
    zachet: 'Зачёт',
    position: 'Позиция',
    driver: 'Водитель',
    prize: 'Приз',
    amount: 'Сумма',
    trips: 'Поездок',
    city: 'Город',
    park: 'Таксопарк',
    license: 'Номер ВУ',
    driver_id: 'ID водителя',
};

/* Порядок по умолчанию — порядок файла: свежая неделя, лист, место. */
export const DEFAULT_SORT = { sort: 'week', dir: 'desc' };

/* Колонки, которые по первому нажатию сортируются от большего: человеку,
   нажавшему «Сумма», нужен верх рейтинга, а не его хвост. */
const DESC_FIRST = new Set(['week', 'amount', 'trips', 'prize']);

export const nextSort = (current, key) => {
    if (current.sort === key) return { sort: key, dir: current.dir === 'desc' ? 'asc' : 'desc' };
    return { sort: key, dir: DESC_FIRST.has(key) ? 'desc' : 'asc' };
};

/* Диапазоны: ключи — те же, что filters.RANGES на сервере. */
export const RANGE_FIELDS = [
    { key: 'position', label: 'Позиция' },
    { key: 'amount', label: 'Сумма, ₸' },
    { key: 'trips', label: 'Поездок' },
    { key: 'prize_amount', label: 'Сумма приза, ₸' },
];

export const EMPTY_FILTERS = Object.freeze({
    q: '',
    period: '',
    zachet: '',
    city: '',
    park: '',
    prize: '',
    list: '',
    ...Object.fromEntries(RANGE_FIELDS.flatMap(({ key }) => [[`${key}_min`, ''], [`${key}_max`, '']])),
});

const FILTER_KEYS = Object.keys(EMPTY_FILTERS);

/* Листы — как в Excel: открыт один лист, вкладки листов над таблицей. «Все
   листы» — отдельная вкладка: её значение ALL_SHEETS, а пустой зачёт значит
   «лист ещё не выбран» (раздел откроет первый, как Excel открывает файл). На
   сервер оба уходят как «без зачёта». */
export const ALL_SHEETS = '*';

export const isAllSheets = (zachet) => !zachet || zachet === ALL_SHEETS;

/* Поиск по ФИО/ВУ/ID и вставленный список — поиск по ВСЕЙ книге, как «Найти
   всё» в Excel: пока он идёт, раздел показывает все листы. */
export const isSearching = (filters) => Boolean(String(filters?.q ?? '').trim() || String(filters?.list ?? '').trim());

/* ── Числа и подписи ─────────────────────────────────────────────────────── */

export const plural = (n, one, few, many) => {
    const mod10 = Math.abs(n) % 10;
    const mod100 = Math.abs(n) % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
};

const NBSP = ' ';
const THIN = ' ';

/** 2767000 → «2 767 000» (узкий неразрывный пробел: число не рвётся строкой). */
export const formatInt = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '';
    return String(Math.trunc(n)).replace(/\B(?=(\d{3})+(?!\d))/g, THIN);
};

export const formatMoney = (value) => `${formatInt(value)}${NBSP}₸`;

const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
    'сентября', 'октября', 'ноября', 'декабря'];

const parseIsoDate = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
    if (!match) return null;
    return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
};

/** «21–27 сентября» / «28 сентября – 4 октября». */
export const periodLabel = (start, end) => {
    const a = parseIsoDate(start);
    const b = parseIsoDate(end);
    if (!a) return '';
    if (!b) return `${a.day} ${MONTHS_GENITIVE[a.month - 1]}`;
    if (a.month === b.month && a.year === b.year) return `${a.day}–${b.day} ${MONTHS_GENITIVE[b.month - 1]}`;
    return `${a.day} ${MONTHS_GENITIVE[a.month - 1]} – ${b.day} ${MONTHS_GENITIVE[b.month - 1]}`;
};

/** «21.09» — короткая дата для колонки «Неделя». */
export const dayMonth = (value) => {
    const d = parseIsoDate(value);
    return d ? `${String(d.day).padStart(2, '0')}.${String(d.month).padStart(2, '0')}` : '';
};

/** «21–27.09» / «28.09–04.10» — неделя в колонке таблицы. */
export const weekShort = (start, end) => {
    const a = parseIsoDate(start);
    const b = parseIsoDate(end);
    if (!a) return '';
    const dd = (d) => String(d.day).padStart(2, '0');
    const mm = (d) => String(d.month).padStart(2, '0');
    if (!b) return `${dd(a)}.${mm(a)}`;
    if (a.month === b.month) return `${dd(a)}–${dd(b)}.${mm(b)}`;
    return `${dd(a)}.${mm(a)}–${dd(b)}.${mm(b)}`;
};

export const weekLabel = (week) => {
    if (!week) return '';
    const period = periodLabel(week.period_start, week.period_end);
    return week.week_number ? `Неделя ${week.week_number} · ${period}` : period;
};

/** «2026-10-02T12:05:00» → «02.10.2026, 12:05» (часы Алматы как есть). */
export const fmtStamp = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(value || ''));
    if (!match) return '';
    return `${match[3]}.${match[2]}.${match[1]}, ${match[4]}:${match[5]}`;
};

export const shortId = (value) => {
    const text = String(value || '');
    return text.length > 10 ? `${text.slice(0, 8)}…` : text;
};

/* ── Диапазоны ───────────────────────────────────────────────────────────── */

/** Целое из поля диапазона: '' — не задано, NaN — набрано не число. */
export const parseWhole = (value) => {
    const text = String(value ?? '').replace(/[\s  ]/g, '');
    if (!text) return '';
    return /^\d+$/.test(text) ? Number(text) : NaN;
};

export const rangeErrors = (filters) => {
    const errors = {};
    RANGE_FIELDS.forEach(({ key }) => {
        ['min', 'max'].forEach((side) => {
            const name = `${key}_${side}`;
            if (Number.isNaN(parseWhole(filters[name]))) errors[name] = 'Только целое число';
        });
    });
    return errors;
};

/* ── Вставленный список ──────────────────────────────────────────────────── */

const HEX_ID = /^[0-9a-fA-F]{32}$/;

const looksLikeKey = (value) => {
    if (HEX_ID.test(value)) return true;
    const key = value.replace(/[\s\-–—_.]/g, '');
    return key.length >= 6 && /\d/.test(key) && /^[0-9A-Za-zА-Яа-яЁё]+$/.test(key);
};

/** Вставленный текст → значения. Правило то же, что filters.split_tokens на
 *  сервере: строки, запятые, «;» и табуляция делят всегда, пробел — только
 *  когда каждая часть сама похожа на ID или номер ВУ. */
export const splitTokens = (text) => {
    const tokens = [];
    String(text || '').split(/[\n\r,;\t]+/).forEach((raw) => {
        const piece = raw.trim();
        if (!piece) return;
        const parts = piece.split(/\s+/);
        if (parts.length > 1 && parts.every(looksLikeKey)) tokens.push(...parts);
        else tokens.push(piece);
    });
    return [...new Set(tokens)];
};

/* ── Фильтры ─────────────────────────────────────────────────────────────── */

/** Тело запроса: только заданные фильтры, числа диапазонов — числами. */
export const filtersPayload = (filters) => {
    const out = {};
    FILTER_KEYS.forEach((key) => {
        const value = filters?.[key];
        if (value === undefined || value === null || value === '') return;
        if (key === 'zachet' && value === ALL_SHEETS) return;
        if (key.endsWith('_min') || key.endsWith('_max')) {
            const number = parseWhole(value);
            if (number !== '' && !Number.isNaN(number)) out[key] = number;
            return;
        }
        const text = String(value).trim();
        if (text) out[key] = text;
    });
    return out;
};

/** Сколько фильтров задано в панели. Поиск, неделя и зачёт — снаружи панели
 *  (строка инструментов и полоса зачётов) и здесь не считаются. */
export const panelFilterCount = (filters) => {
    let count = 0;
    ['city', 'park', 'prize'].forEach((key) => { if (filters[key]) count += 1; });
    RANGE_FIELDS.forEach(({ key }) => {
        if (String(filters[`${key}_min`] ?? '').trim() || String(filters[`${key}_max`] ?? '').trim()) count += 1;
    });
    return count;
};

const rangeText = (min, max) => {
    const a = parseWhole(min);
    const b = parseWhole(max);
    const has = (v) => v !== '' && !Number.isNaN(v);
    if (has(a) && has(b)) return `${formatInt(a)}–${formatInt(b)}`;
    if (has(a)) return `от ${formatInt(a)}`;
    if (has(b)) return `до ${formatInt(b)}`;
    return '';
};

export const prizeLabel = (value) => {
    if (value === PRIZE_ANY) return 'Только с призом';
    if (value === PRIZE_NONE) return 'Без приза';
    return value;
};

/** Чипы выбранного: что отобрано, видно, не открывая панель. Зачёта среди них
 *  нет — выбранный зачёт и так подсвечен в полосе зачётов над таблицей. */
export const filterChips = (filters) => {
    const chips = [];
    const add = (key, name, label, clear) => { if (label) chips.push({ key, name, label, clear }); };
    add('city', 'Город', filters.city, { city: '' });
    add('park', 'Таксопарк', filters.park, { park: '' });
    add('prize', 'Приз', filters.prize ? prizeLabel(filters.prize) : '', { prize: '' });
    RANGE_FIELDS.forEach(({ key, label }) => {
        add(key, label.replace(/, ₸$/, ''), rangeText(filters[`${key}_min`], filters[`${key}_max`]),
            { [`${key}_min`]: '', [`${key}_max`]: '' });
    });
    return chips;
};

export const hasAnyFilter = (filters) => FILTER_KEYS.some((key) => String(filters?.[key] ?? '').trim() !== '');

/*
 * Группы таблицы по зачётам — как листы файла. Строка-заголовок ставится
 * перед первой строкой каждой группы, когда список идёт в порядке файла
 * (неделя → лист → место) и зачёт не выбран: только тогда строки одного
 * зачёта стоят подряд. При сортировке по сумме, ФИО и т. п. зачёты
 * перемешиваются — там список один, а зачёт виден колонкой.
 */
export const GROUPED_SORTS = ['week', 'zachet'];

export const isGrouped = (filters, sort) => isAllSheets(filters?.zachet) && GROUPED_SORTS.includes(sort?.sort);

/** Строки страницы → [{ type: 'group', key, zachet, period_start } | { type: 'row', row }]. */
export const groupRows = (rows, withWeek) => {
    const out = [];
    let previous = null;
    (rows || []).forEach((row) => {
        const key = withWeek ? `${row.period_start}|${row.zachet}` : row.zachet;
        if (key !== previous) {
            out.push({ type: 'group', key, zachet: row.zachet, period_start: row.period_start });
            previous = key;
        }
        out.push({ type: 'row', row });
    });
    return out;
};

/* ── Адрес ───────────────────────────────────────────────────────────────── */
/*
 * Фильтры живут в адресе (п. 5 постановки): ссылку на выборку можно прислать
 * коллеге, перезагрузка её не теряет. Все метки раздела — с приставкой «bg_»:
 * так их не спутать с метками других разделов, а App.jsx снимает их разом,
 * когда человек уходит из раздела (stripBaigaParams).
 */
export const PARAM_PREFIX = 'bg_';

/* Поиск (ФИО, номер ВУ, ID) и вставленный список в адрес НЕ пишутся: это
   персональные данные — они остались бы в истории браузера общего компьютера и
   в аналитике посещений, а список в сотни ID делает адрес длиннее предела, и
   страница переставала бы открываться. В адресе — неделя, лист, город, парк,
   приз, диапазоны, порядок и размер страницы. */
const URL_KEYS = FILTER_KEYS.filter((key) => key !== 'q' && key !== 'list');

export const readStateFromSearch = (search) => {
    const params = new URLSearchParams(search || '');
    const filters = { ...EMPTY_FILTERS };
    URL_KEYS.forEach((key) => {
        const value = params.get(PARAM_PREFIX + key);
        if (value !== null) filters[key] = value;
    });
    const sort = params.get(`${PARAM_PREFIX}sort`) || '';
    const [sortKey, dir] = sort.split('.');
    const size = Number(params.get(`${PARAM_PREFIX}size`) || 0);
    return {
        filters,
        sort: COLUMN_LABELS[sortKey] ? { sort: sortKey, dir: dir === 'asc' ? 'asc' : 'desc' } : { ...DEFAULT_SORT },
        size: PAGE_SIZES.includes(size) ? size : DEFAULT_PAGE_SIZE,
    };
};

/** Метки раздела на готовом URL. Возвращает тот же объект. */
export const applyStateToUrl = (url, { filters, sort, size }) => {
    stripBaigaParams(url);
    URL_KEYS.forEach((key) => {
        const value = String(filters?.[key] ?? '').trim();
        if (value) url.searchParams.set(PARAM_PREFIX + key, value);
    });
    if (sort && (sort.sort !== DEFAULT_SORT.sort || sort.dir !== DEFAULT_SORT.dir)) {
        url.searchParams.set(`${PARAM_PREFIX}sort`, `${sort.sort}.${sort.dir}`);
    }
    if (size && size !== DEFAULT_PAGE_SIZE) url.searchParams.set(`${PARAM_PREFIX}size`, String(size));
    return url;
};

/** Снять все метки раздела. true — было что снимать. */
export const stripBaigaParams = (url) => {
    if (!url || !url.searchParams) return false;
    const keys = [];
    url.searchParams.forEach((_value, key) => { if (key.startsWith(PARAM_PREFIX)) keys.push(key); });
    keys.forEach((key) => url.searchParams.delete(key));
    return keys.length > 0;
};

export const writeStateToAddressBar = (state) => {
    if (typeof window === 'undefined') return;
    try {
        const url = new URL(window.location.href);
        stripTechnicalQueryParams(url);
        applyStateToUrl(url, state);
        const next = `${url.pathname}${url.search}${url.hash}`;
        const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        if (next !== current) window.history.replaceState(window.history.state, '', next);
    } catch (error) {
        // Адрес — удобство, а не данные: без него раздел работает.
    }
};

/* ── Файл ────────────────────────────────────────────────────────────────── */

/** Имя файла из Content-Disposition (filename*=UTF-8''…), иначе запасное. */
export const fileNameFromDisposition = (header, fallback) => {
    const text = String(header || '');
    const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(text);
    if (star) {
        try { return decodeURIComponent(star[1].trim().replace(/^"|"$/g, '')); } catch (error) { /* ниже */ }
    }
    const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(text);
    return plain ? plain[1].trim() : fallback;
};
