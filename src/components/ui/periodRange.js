/* Период «с — по» строками ГГГГ-ММ-ДД — правила без React.
 *
 * Календарь периода в портале один (IosDateRangeCalendar), но «Аналитика»
 * журнала оценок живёт в своей сборке без Tailwind и рисует календарь своей
 * разметкой (src/call_evaluation/EvalPeriodPicker.jsx). Чтобы два календаря не
 * разошлись в правилах — что значит «Неделя», какой период считается целым
 * месяцем, как его подписать, какие месяцы и годы уже недоступны, — правила
 * лежат здесь. Проверяет их node-тест tests/period_range.test.mjs.
 *
 * Задача #389: выбор года, месяца, конкретного дня или диапазона дат и быстрые
 * периоды «Сегодня», «Неделя», «Месяц», «Весь период». Пустые границы ('' и '')
 * — это «Весь период», то есть фильтра нет. */

export const MONTH_NAMES = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
/* В сетке месяцев — три буквы с заглавной: «Май» и «Июн» в одну ширину. */
export const MONTH_SHORT = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
    'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];
const MONTH_SHORT_LOWER = ['янв', 'фев', 'мар', 'апр', 'май', 'июн',
    'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

/* Виды календаря периода: дни (день или диапазон), месяцы, годы. */
export const PERIOD_VIEWS = ['days', 'months', 'years'];
export const PERIOD_VIEW_LABELS = { days: 'Дни', months: 'Месяцы', years: 'Годы' };

/* Годы — страницей по двенадцать, той же сеткой 3 × 4, что и месяцы: вид не
   меняет размер карточки, когда переключаешь «Месяцы» и «Годы». */
export const YEAR_PAGE = 12;

const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const pad = (n) => String(n).padStart(2, '0');

export const isoDate = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

/** Дата из «ГГГГ-ММ-ДД» в местном времени; null — не дата. */
export const parseIso = (iso) => {
    const match = ISO_RE.exec(String(iso || ''));
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return isoDate(date) === match[0] ? date : null;
};

/** «ГГГГ-ММ-ДД» со сдвигом на days дней. */
export const shiftIso = (iso, days) => {
    const date = parseIso(iso);
    if (!date) return '';
    date.setDate(date.getDate() + days);
    return isoDate(date);
};

export const daysInMonth = (year, month0) => new Date(year, month0 + 1, 0).getDate();

/** Границы календарного месяца: month0 — 0…11. */
export const monthRange = (year, month0) => ({
    from: `${year}-${pad(month0 + 1)}-01`,
    to: `${year}-${pad(month0 + 1)}-${pad(daysInMonth(year, month0))}`,
});

/** Границы календарного года. */
export const yearRange = (year) => ({ from: `${year}-01-01`, to: `${year}-12-31` });

/** «ГГГГ-ММ», если период — ровно один календарный месяц, иначе null. */
export const wholeMonthOf = (from, to) => {
    const start = parseIso(from);
    if (!start || !parseIso(to) || start.getDate() !== 1) return null;
    const range = monthRange(start.getFullYear(), start.getMonth());
    return range.to === to ? from.slice(0, 7) : null;
};

/** «ГГГГ», если период — ровно один календарный год, иначе null. */
export const wholeYearOf = (from, to) => {
    if (!parseIso(from) || !parseIso(to)) return null;
    const year = from.slice(0, 4);
    return from === `${year}-01-01` && to === `${year}-12-31` ? year : null;
};

/** Период из ЦЕЛЫХ месяцев (с 1-го числа по последний день) — месяцы «ГГГГ-ММ»
    по порядку; иначе null. Журнал оценок считает такие периоды по месяцу
    журнала, как раньше считал один месяц, а остальные — по дате обращения. */
export const wholeMonthsOf = (from, to) => {
    const start = parseIso(from);
    const end = parseIso(to);
    if (!start || !end || from > to || start.getDate() !== 1) return null;
    if (monthRange(end.getFullYear(), end.getMonth()).to !== to) return null;
    const months = [];
    let year = start.getFullYear();
    let month0 = start.getMonth();
    const last = to.slice(0, 7);
    for (;;) {
        const key = `${year}-${pad(month0 + 1)}`;
        months.push(key);
        if (key >= last) break;
        month0 += 1;
        if (month0 === 12) { month0 = 0; year += 1; }
    }
    return months;
};

const shortDate = (iso, withYear) => {
    const [y, m, d] = iso.split('-').map(Number);
    return `${d} ${MONTH_SHORT_LOWER[m - 1]}${withYear ? ` ${y}` : ''}`;
};

/** Подпись выбранного периода на кнопке календаря. */
export const periodLabel = (from, to) => {
    if (!from && !to) return 'Весь период';
    if (from && to) {
        const year = wholeYearOf(from, to);
        if (year) return `${year} год`;
        const month = wholeMonthOf(from, to);
        if (month) return `${MONTH_NAMES[Number(month.slice(5, 7)) - 1]} ${month.slice(0, 4)}`;
        if (from === to) return shortDate(from, true);
        const sameYear = from.slice(0, 4) === to.slice(0, 4);
        return `${shortDate(from, !sameYear)} — ${shortDate(to, true)}`;
    }
    return from ? `с ${shortDate(from, true)}` : `по ${shortDate(to, true)}`;
};

/** Быстрые периоды (#389). «Неделя» и «Месяц» — скользящие 7 и 30 дней по
    сегодня, как в примере заказчицы (панель фильтров «Звонков» и «Чатов»);
    «Весь период» снимает фильтр. Дата считается в момент нажатия, а не при
    отрисовке: открытая с вечера страница наутро дала бы вчерашнее «Сегодня».
    `now` — только для тестов. */
export const quickPeriods = (now = null) => {
    const today = () => isoDate(now ? new Date(now) : new Date());
    return [
        { key: 'today', label: 'Сегодня', range: () => ({ from: today(), to: today() }) },
        { key: 'week', label: 'Неделя', range: () => ({ from: shiftIso(today(), -6), to: today() }) },
        { key: 'month', label: 'Месяц', range: () => ({ from: shiftIso(today(), -29), to: today() }) },
        { key: 'all', label: 'Весь период', range: () => ({ from: '', to: '' }) },
    ];
};

/** Совпадает ли выбор с быстрым периодом — его кнопка подсвечивается. */
export const presetMatches = (preset, from, to) => {
    const range = preset?.range?.();
    return Boolean(range) && (range.from || '') === (from || '') && (range.to || '') === (to || '');
};

/** Понедельник — первый день недели: 0…6 для 1-го числа месяца. */
export const firstWeekday = (year, month0) => {
    const day = new Date(year, month0, 1).getDay();
    return day === 0 ? 6 : day - 1;
};

/** Края подсветки выбора: на шаге «конец периода» полоса тянется за курсором. */
export const selectionBand = (draft, step, hover) => {
    let lo = draft?.from || '';
    let hi = draft?.to || '';
    if (step === 1 && hover && lo) {
        if (hover < lo) { hi = lo; lo = hover; } else { hi = hover; }
    }
    return { lo, hi, hasRange: Boolean(lo && hi && lo !== hi) };
};

/** Клетки сетки месяца: пустые до 1-го числа, дни месяца, а с fill — пустые
    до шести недель (42 клетки), чтобы карточка не прыгала при листании. День
    знает, недоступен ли он, край ли он выбора, внутри ли полосы, сегодня ли. */
export const dayCells = (year, month0, { lo = '', hi = '', min = '', max = '', today = '', fill = false } = {}) => {
    const cells = [];
    const lead = firstWeekday(year, month0);
    for (let index = 0; index < lead; index += 1) cells.push({ key: `e${index}`, iso: null });
    const hasRange = Boolean(lo && hi && lo !== hi);
    for (let day = 1; day <= daysInMonth(year, month0); day += 1) {
        const iso = `${year}-${pad(month0 + 1)}-${pad(day)}`;
        cells.push({
            key: iso, iso, day,
            disabled: Boolean((max && iso > max) || (min && iso < min)),
            isEnd: iso === lo || iso === hi,
            inBand: hasRange && iso > lo && iso < hi,
            bandStart: hasRange && iso === lo,
            bandEnd: hasRange && iso === hi,
            today: iso === today,
        });
    }
    if (fill) while (cells.length < 42) cells.push({ key: `t${cells.length}`, iso: null });
    return cells;
};

/** Выбор дня в диапазоне: первое нажатие — начало (шаг 1), второе — конец;
    тот же день вторым нажатием — этот один день. Возвращает следующее
    состояние и, когда выбор готов, его границы (`done`). */
export const pickDayStep = (draft, step, iso) => {
    if (step === 0) return { draft: { from: iso, to: iso }, step: 1, done: null };
    const start = draft?.from || iso;
    const next = iso < start ? { from: iso, to: start } : { from: start, to: iso };
    return { draft: next, step: 0, done: next };
};

/** С какого вида открыть календарь: целый год — «Годы», целый месяц —
    «Месяцы», иначе — дни. Человек видит свой выбор отмеченным сразу. */
export const initialPeriodView = (from, to, views = PERIOD_VIEWS) => {
    if (views.includes('years') && wholeYearOf(from, to)) return 'years';
    if (views.includes('months') && wholeMonthOf(from, to)) return 'months';
    return 'days';
};

/** Двенадцать месяцев года для вида «Месяцы». Месяц недоступен, только если
    ЦЕЛИКОМ вне границ: текущий месяц при max = сегодня выбрать можно. */
export const monthCells = (year, { from = '', to = '', min = '', max = '', today = '' } = {}) => {
    const selected = wholeMonthOf(from, to);
    return MONTH_SHORT.map((label, month0) => {
        const range = monthRange(year, month0);
        const key = range.from.slice(0, 7);
        return {
            key, label, title: `${MONTH_NAMES[month0]} ${year}`, range,
            disabled: Boolean((max && range.from > max) || (min && range.to < min)),
            selected: selected === key,
            current: Boolean(today) && today.slice(0, 7) === key,
        };
    });
};

/** Последний год страницы, на которой виден год year: страницы идут по
    YEAR_PAGE лет и кончаются годом anchor (обычно — годом max или текущим). */
export const yearPageEnd = (year, anchor) => {
    if (year > anchor) return anchor + Math.ceil((year - anchor) / YEAR_PAGE) * YEAR_PAGE;
    return anchor - Math.floor((anchor - year) / YEAR_PAGE) * YEAR_PAGE;
};

/** Годы страницы, кончающейся годом endYear, — для вида «Годы». */
export const yearCells = (endYear, { from = '', to = '', min = '', max = '', today = '' } = {}) => {
    const selected = wholeYearOf(from, to);
    return Array.from({ length: YEAR_PAGE }, (_, index) => endYear - YEAR_PAGE + 1 + index).map((year) => {
        const range = yearRange(year);
        return {
            key: String(year), label: String(year), title: `${year} год`, range,
            disabled: Boolean((max && range.from > max) || (min && range.to < min)),
            selected: selected === String(year),
            current: Boolean(today) && today.slice(0, 4) === String(year),
        };
    });
};
