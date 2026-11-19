/*
 * Табель фронт-офисов в «Учете часов» (ТЗ «выгрузка табеля», 06.11.2026).
 *
 * Решение владельца: табель только про отдел «Фронт офисы» («Регионы»),
 * выгружают глава этого отдела и суперадмины. Сервер проверяет то же самое
 * (front_office_timesheet/routes.py), здесь — только что показывать.
 *
 * Правила без React: кому плитка и страница, как собрать запрос, как назвать
 * файл. Проверяются тестом без браузера (tests/front_office_timesheet.test.mjs).
 */

export const TIMESHEET_SCOPE = 'front_office_timesheet';
// Отбор «Город не указан»: пустая строка в запросе неотличима от «без отбора».
export const TIMESHEET_NO_CITY = '__none__';
const FRONT_OFFICE_CODE = 'front_office';

const normalizeRole = (role) => {
    const value = String(role || '').trim().toLowerCase();
    return ['superadmin', 'super-admin', 'super admin'].includes(value) ? 'super_admin' : value;
};

export const isSuperAdminUser = (user) => normalizeRole(user?.role) === 'super_admin';

/* Глава отдела «Фронт офисы»: код ищем и в списке возглавляемых отделов, и в
   одиночном поле — у части профилей приходит только одно из двух. */
export const headsFrontOfficeDepartment = (user) => {
    const codes = [];
    const plural = user?.headed_department_codes ?? user?.headedDepartmentCodes;
    if (Array.isArray(plural)) codes.push(...plural);
    codes.push(user?.headed_department_code ?? user?.headedDepartmentCode);
    return codes.some((code) => String(code || '').trim().toLowerCase() === FRONT_OFFICE_CODE);
};

export const canExportFrontOfficeTimesheet = (user) => (
    isSuperAdminUser(user) || headsFrontOfficeDepartment(user)
);

/* Табель на экране «Учета часов» (просьба владельца 06.11.2026: «при выборе группы
   регионов выводилось на сайте тоже, в стилистике сайта»). Режим доступен, когда
   выбрана группа отдела фронт-офисов, а у главы фронт-офисов — и без группы: её
   раздел и есть этот отдел (сервер «Учета часов» режет главу её отделом).
   departmentId — id отдела фронт-офисов с сервера (/api/front_office_timesheet/scope). */
export function timesheetAvailable({ canExport, departmentId, selectedGroup, selectedGroupId, headedDepartmentId }) {
    if (!canExport || departmentId == null) return false;
    const target = Number(departmentId);
    if (selectedGroupId) {
        const groupDepartment = selectedGroup?.department_id ?? selectedGroup?.departmentId;
        return groupDepartment != null && Number(groupDepartment) === target;
    }
    return headedDepartmentId != null && Number(headedDepartmentId) === target;
}

const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
    'августа', 'сентября', 'октября', 'ноября', 'декабря'];

/* Как показать ячейку дня. tone — смысл для цвета: рабочий день нейтральный
   (его большинство, красить нечего), выходной приглушён, отсутствия — мягкими
   плашками. */
export function timesheetCellView(cell) {
    const code = String(cell?.code || '');
    const tone = {
        'р': 'work', 'в': 'off', 'Б/С': 'bs', 'О': 'vacation', 'Б': 'sick', 'н': 'absent',
    }[code] || 'empty';
    return {
        label: code,
        tone,
        incomplete: Boolean(cell?.incomplete) && code === 'р',
        late: Number(cell?.late || 0),
        early: Number(cell?.early || 0),
    };
}

const CODE_TITLES = {
    'р': 'рабочий день',
    'в': 'выходной',
    'Б/С': 'без содержания',
    'О': 'отпуск',
    'Б': 'больничный',
    'н': 'смена без отметок',
};

/* Подсказка ячейки: дата, что за день и откуда числа. */
export function timesheetCellTitle(day, cell) {
    const parts = [];
    const date = String(day?.date || '');
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
    parts.push(match ? `${Number(match[3])} ${MONTHS_GENITIVE[Number(match[2]) - 1]}, ${day?.weekday || ''}`.trim() : date);
    const code = String(cell?.code || '');
    if (code) parts.push(CODE_TITLES[code] || code);
    else if (day?.future) parts.push('ещё не наступил');
    if (code === 'р') {
        const span = [cell?.arrive, cell?.leave].map((value) => value || '—').join('–');
        parts.push(`отметки ${span}`);
        if (cell?.incomplete) parts.push(cell?.arrive ? 'нет отметки ухода' : 'нет отметки прихода');
    }
    if (Number(cell?.late) > 0) parts.push(`опоздание ${cell.late} мин`);
    if (Number(cell?.early) > 0) parts.push(`ранний уход ${cell.early} мин`);
    if (day?.holiday) parts.push('праздник');
    return parts.join(' · ');
}

/* Итоги под таблицей: сколько человек отработали каждый день и суммы колонок. */
export function timesheetColumnTotals(payload) {
    const days = Array.isArray(payload?.days) ? payload.days : [];
    const perDay = days.map(() => 0);
    const sums = { worked: 0, missed: 0, late: 0, early: 0, people: 0 };
    for (const city of payload?.cities || []) {
        for (const person of city?.people || []) {
            sums.people += 1;
            (person?.cells || []).forEach((cell, index) => {
                if (cell?.code === 'р' && index < perDay.length) perDay[index] += 1;
            });
            for (const key of ['worked', 'missed', 'late', 'early']) {
                sums[key] += Number(person?.totals?.[key] || 0);
            }
        }
    }
    return { perDay, ...sums };
}

export const TIMESHEET_LEGEND = [
    'р — рабочий день по отметкам «Отметок»; точка — нет отметки прихода или ухода.',
    'в — выходной, Б/С — без содержания, О — отпуск, Б — больничный, н — смена без отметок.',
    'Красное число — опоздание, мин; оранжевое — ранний уход, мин. Уход, после которого человек снова вошёл, ранним уходом не считается.',
    'Файл — «Сформировать отчёт» → «Табель фронт-офисов»: тот же период и отбор, что на экране.',
];

export const emptyTimesheetFilters = () => ({ cities: [], userIds: [], jobTitles: [] });

export const hasTimesheetFilters = (filters) => Boolean(
    filters?.cities?.length || filters?.userIds?.length || filters?.jobTitles?.length,
);

/* ── Период «с — по» ───────────────────────────────────────────────────────
   Просьба владельца 19.11.2026: табель «Регионов» не только за месяц, а за
   период — от одного дня до месяца («месяц это максимум»). Период — строки
   ГГГГ-ММ-ДД { from, to }; месяц считается от дня начала: с 1-го числа — ровно
   этот месяц, с 15 октября — по 14 ноября. Двойник правила на сервере —
   rules.period_max_end (front_office_timesheet/rules.py): что календарь здесь
   разрешил, то сервер и примет. */

const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const MONTH_RE = /^(\d{4})-(\d{2})$/;
const MONTHS_NOMINATIVE = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль',
    'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
const pad2 = (value) => String(value).padStart(2, '0');
const isoOf = (year, month, day) => `${year}-${pad2(month)}-${pad2(day)}`;
// month — 1…12
const daysInMonth = (year, month) => new Date(year, month, 0).getDate();

function parseDay(iso) {
    const match = DAY_RE.exec(String(iso || ''));
    if (!match) return null;
    const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
    if (month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month)) return null;
    return { year, month, day };
}

function shiftDay(iso, days) {
    const parts = parseDay(iso);
    if (!parts) return '';
    const date = new Date(parts.year, parts.month - 1, parts.day + days);
    return isoOf(date.getFullYear(), date.getMonth() + 1, date.getDate());
}

const dayCount = (from, to) => {
    const a = parseDay(from);
    const b = parseDay(to);
    if (!a || !b) return 0;
    return Math.round((Date.UTC(b.year, b.month - 1, b.day) - Date.UTC(a.year, a.month - 1, a.day)) / 86400000) + 1;
};

/* Месяц страницы целиком: '2026-11' → { from: '2026-11-01', to: '2026-11-30' }. */
export function wholeMonthPeriod(month) {
    const match = MONTH_RE.exec(String(month || '').trim());
    if (!match) return null;
    const [year, monthNumber] = [Number(match[1]), Number(match[2])];
    if (monthNumber < 1 || monthNumber > 12) return null;
    return { from: isoOf(year, monthNumber, 1), to: isoOf(year, monthNumber, daysInMonth(year, monthNumber)) };
}

/* 'ГГГГ-ММ', если период — ровно календарный месяц, иначе null. */
export function wholeMonthOfPeriod(period) {
    const start = parseDay(period?.from);
    if (!start || start.day !== 1) return null;
    const month = `${start.year}-${pad2(start.month)}`;
    return wholeMonthPeriod(month)?.to === period?.to ? month : null;
}

/* Месяц, к которому период относится на странице: месяц его начала. */
export const periodMonth = (period) => (parseDay(period?.from) ? String(period.from).slice(0, 7) : '');

/* Последний день периода не длиннее месяца, который начинается в from: день
   перед тем же числом следующего месяца, а если такого числа там нет (с 31
   октября — в ноябре 30 дней) — последний день следующего месяца. */
export function periodMaxEnd(from) {
    const start = parseDay(from);
    if (!start) return '';
    const year = start.month === 12 ? start.year + 1 : start.year;
    const month = start.month === 12 ? 1 : start.month + 1;
    const last = daysInMonth(year, month);
    if (start.day <= last) return shiftDay(isoOf(year, month, start.day), -1);
    return isoOf(year, month, last);
}

/* Самое раннее начало, от которого месяц ещё достаёт до to: нужно календарю,
   когда концом выбирают день раньше начала. Месяц — не больше 31 дня, поэтому
   хватает перебора. */
export function periodMinStart(to) {
    if (!parseDay(to)) return '';
    for (let back = 30; back > 0; back -= 1) {
        const start = shiftDay(to, -back);
        if (periodMaxEnd(start) >= to) return start;
    }
    return to;
}

/* Какие дни календарь разрешает вторым нажатием, когда первое — anchor. */
export const timesheetSpanLimit = (anchor) => ({ min: periodMinStart(anchor), max: periodMaxEnd(anchor) });

/* Период годен для запроса: обе даты, начало не позже конца, не длиннее месяца. */
export function isValidTimesheetPeriod(period) {
    const { from, to } = period || {};
    if (!parseDay(from) || !parseDay(to) || from > to) return false;
    return to <= periodMaxEnd(from);
}

/* Соседний период того же размера — стрелки ‹ › у кнопки периода. Целый месяц
   листается месяцами (как прежний выбор месяца); полный «месяц от N-го» (с 15-го
   по 14-е) — тоже месяцами, от того же числа, иначе после февраля 31 день
   навсегда сжимался бы в 28 (разбор 19.11.2026); остальные — на свою длину:
   день — по дню, неделя — по неделе. Конец не выходит за месяц от нового начала. */
export function shiftTimesheetPeriod(period, direction) {
    const step = direction < 0 ? -1 : 1;
    const month = wholeMonthOfPeriod(period);
    if (month) {
        const [year, monthNumber] = month.split('-').map(Number);
        const date = new Date(year, monthNumber - 1 + step, 1);
        return wholeMonthPeriod(`${date.getFullYear()}-${pad2(date.getMonth() + 1)}`);
    }
    if (!isValidTimesheetPeriod(period)) return period;
    if (period.to === periodMaxEnd(period.from)) {
        const start = parseDay(period.from);
        const target = new Date(start.year, start.month - 1 + step, 1);
        const year = target.getFullYear();
        const monthNumber = target.getMonth() + 1;
        const from = isoOf(year, monthNumber, Math.min(start.day, daysInMonth(year, monthNumber)));
        return { from, to: periodMaxEnd(from) };
    }
    const length = dayCount(period.from, period.to);
    const from = shiftDay(period.from, step * length);
    const to = shiftDay(from, length - 1);
    const limit = periodMaxEnd(from);
    return { from, to: to > limit ? limit : to };
}

/* Подпись периода — двойник rules.period_title: «октябрь 2026», «5 ноября 2026»,
   «1–15 ноября 2026», «25 октября – 5 ноября 2026». */
export function timesheetPeriodTitle(period) {
    const start = parseDay(period?.from);
    const end = parseDay(period?.to);
    if (!start || !end) return '';
    if (wholeMonthOfPeriod(period)) return `${MONTHS_NOMINATIVE[start.month - 1]} ${start.year}`;
    if (period.from === period.to) return `${start.day} ${MONTHS_GENITIVE[start.month - 1]} ${start.year}`;
    if (start.year === end.year && start.month === end.month) {
        return `${start.day}–${end.day} ${MONTHS_GENITIVE[end.month - 1]} ${end.year}`;
    }
    if (start.year === end.year) {
        return `${start.day} ${MONTHS_GENITIVE[start.month - 1]} – ${end.day} ${MONTHS_GENITIVE[end.month - 1]} ${end.year}`;
    }
    return `${start.day} ${MONTHS_GENITIVE[start.month - 1]} ${start.year} – `
        + `${end.day} ${MONTHS_GENITIVE[end.month - 1]} ${end.year}`;
}

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const capitalize = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text);

/* Подпись поля «Период» в окне отчёта — тот же текст, что в заголовке окна
   («Отчёт за 3–14 ноября 2026»), с заглавной: один период — одна запись. */
export const timesheetPeriodFieldLabel = (period) => capitalize(timesheetPeriodTitle(period));

/* Короткая подпись кнопки периода в шапке — «Ноябрь 2026», «5 ноя 2026»,
   «3–14 ноя 2026», «25 окт – 5 ноя 2026», через год — «25 дек – 5 янв 2027» (год
   конца: период не длиннее месяца, начало однозначно). Кнопка постоянной ширины
   (TIMESHEET_PERIOD_BUTTON_WIDTH): самая длинная подпись — «31 янв – 28 фев 2027»,
   133 px при 12.5 px, — помещается, и шапка не прыгает при смене периода. */
export function timesheetPeriodChip(period) {
    const start = parseDay(period?.from);
    const end = parseDay(period?.to);
    if (!start || !end) return '';
    if (wholeMonthOfPeriod(period)) return capitalize(`${MONTHS_NOMINATIVE[start.month - 1]} ${start.year}`);
    if (period.from === period.to) return `${start.day} ${MONTHS_SHORT[start.month - 1]} ${start.year}`;
    if (start.year === end.year && start.month === end.month) {
        return `${start.day}–${end.day} ${MONTHS_SHORT[end.month - 1]} ${end.year}`;
    }
    return `${start.day} ${MONTHS_SHORT[start.month - 1]} – ${end.day} ${MONTHS_SHORT[end.month - 1]} ${end.year}`;
}

/* Ширина выбора периода и выбора месяца в шапке, пока раздел про табель:
   переключение «Табель ↔ показатели» и смена периода не двигают кнопки рядом. */
export const TIMESHEET_PERIOD_BUTTON_WIDTH = 'w-[184px]';

const ruDay = (iso) => {
    const parts = parseDay(iso);
    return parts ? `${pad2(parts.day)}.${pad2(parts.month)}.${parts.year}` : '';
};

/* Имя файла — двойник routes.export_file_name: Content-Disposition браузер фронту
   не отдаёт (его нет в Access-Control-Expose-Headers), имя собирается с двух
   сторон. Месяц — «Табель фронт-офисы 2026-10.xlsx», как было. */
export function timesheetFileName(period) {
    const month = wholeMonthOfPeriod(period);
    if (month) return `Табель фронт-офисы ${month}.xlsx`;
    if (period?.from === period?.to) return `Табель фронт-офисы ${ruDay(period?.from)}.xlsx`;
    return `Табель фронт-офисы ${ruDay(period?.from)} — ${ruDay(period?.to)}.xlsx`;
}

/* Запрос табеля. period — { from, to } или, как раньше, строка месяца 'ГГГГ-ММ'.
   С периодом идёт и месяц его начала: сервер до этой правки дат не знает и
   построит табель за месяц, а не ответит ошибкой. */
export function timesheetQuery(period, filters) {
    const params = new URLSearchParams();
    if (period && typeof period === 'object') {
        params.append('month', periodMonth(period));
        params.append('date_from', String(period.from || ''));
        params.append('date_to', String(period.to || ''));
    } else {
        params.append('month', String(period || ''));
    }
    (filters?.cities || []).forEach((city) => params.append('city', String(city)));
    (filters?.userIds || []).forEach((id) => params.append('user_id', String(id)));
    (filters?.jobTitles || []).forEach((title) => params.append('job_title', String(title)));
    return params.toString();
}

/* Подсказка календаря периода на первом нажатии: о пределе — сразу. */
export const TIMESHEET_PERIOD_HINT = 'Выберите начало: от дня до месяца';

/* Быстрые периоды календаря табеля. «Вчера» — последний день с отметками
   (сегодняшний ещё идёт, факт табель показывает по вчера); «Этот месяц» —
   вернуться к месяцу целиком. Остальное — днями или вкладкой «Месяцы». Дата —
   в момент нажатия, а не при отрисовке. `now` — только для тестов. */
export function timesheetPeriodPresets(now = null) {
    const today = () => {
        const date = now ? new Date(now) : new Date();
        return isoOf(date.getFullYear(), date.getMonth() + 1, date.getDate());
    };
    return [
        { label: 'Вчера', range: () => { const day = shiftDay(today(), -1); return { from: day, to: day }; } },
        { label: 'Этот месяц', range: () => wholeMonthPeriod(today().slice(0, 7)) },
    ];
}

/* Ключ периода для кэша списков отбора. */
export const timesheetPeriodKey = (period) => (isValidTimesheetPeriod(period) ? `${period.from}..${period.to}` : '');

/* Ответ /options → строки трёх списков отбора. Уволенный в месяце в списке
   есть (он в табеле месяца), но приглушён. */
export function timesheetSelectOptions(payload) {
    return {
        cities: (payload?.cities || []).map((city) => ({ value: String(city.value), label: String(city.label) })),
        people: (payload?.people || []).map((person) => ({
            value: String(person.id),
            label: String(person.name || ''),
            meta: person.city || undefined,
            muted: person.active === false,
        })),
        jobs: (payload?.job_titles || []).map((title) => ({ value: String(title), label: String(title) })),
    };
}

/* «Нет карточки Workpace» в окне отчёта — только про тех, кто попадёт в файл
   при текущем отборе: выбрали двух человек — чужие имена в окне были бы шумом.
   Отбор — по правилу сервера (rules.matches_filters): город без учёта регистра,
   «Город не указан» — TIMESHEET_NO_CITY, должность — как пришла (пустая уже
   «менеджер»). Сервер без признака карточки у людей — его готовый список. */
export function timesheetWithoutCard(payload, filters) {
    const people = Array.isArray(payload?.people) ? payload.people : [];
    if (!people.some((person) => typeof person?.card === 'boolean')) {
        return Array.isArray(payload?.without_card) ? payload.without_card : [];
    }
    const fold = (value) => String(value ?? '').trim().toLowerCase();
    const cities = new Set((filters?.cities || []).map(fold));
    const ids = new Set((filters?.userIds || []).map(String));
    const jobs = new Set((filters?.jobTitles || []).map(fold));
    return people
        .filter((person) => {
            if (person?.active === false || person?.card !== false) return false;
            const city = fold(person.city) || TIMESHEET_NO_CITY;
            if (cities.size && !cities.has(city)) return false;
            if (ids.size && !ids.has(String(person.id))) return false;
            if (jobs.size && !jobs.has(fold(person.job_title))) return false;
            return true;
        })
        .map((person) => String(person.name || ''))
        // Порядок — как у сервера над табелем (sorted по str.casefold, то есть по
        // кодам букв): localeCompare ставил бы «Ёлкин» в другое место.
        .sort((left, right) => {
            const a = left.toLowerCase();
            const b = right.toLowerCase();
            return a < b ? -1 : (a > b ? 1 : 0);
        });
}

/* Отбор держит только то, что есть в выбранном месяце: сменили месяц — ушедшие
   люди и города из отбора отпадают, а не уходят в запрос невидимыми. */
export function pruneTimesheetFilters(filters, options) {
    const keep = (values, list) => {
        const allowed = new Set((list || []).map((item) => String(item.value)));
        return (values || []).map(String).filter((value) => allowed.has(value));
    };
    return {
        cities: keep(filters?.cities, options?.cities),
        userIds: keep(filters?.userIds, options?.people),
        jobTitles: keep(filters?.jobTitles, options?.jobs),
    };
}

export function sameTimesheetFilters(left, right) {
    const key = (filters) => JSON.stringify([
        [...(filters?.cities || [])].map(String).sort(),
        [...(filters?.userIds || [])].map(String).sort(),
        [...(filters?.jobTitles || [])].map(String).sort(),
    ]);
    return key(left) === key(right);
}

/* Имя файла из Content-Disposition (filename*=UTF-8''… или filename="…"). */
export function downloadFileName(contentDisposition, fallback) {
    const header = String(contentDisposition || '');
    const encoded = header.match(/filename\*\s*=\s*(?:UTF-8'')?["']?([^;"']+)["']?/i);
    if (encoded?.[1]) {
        try {
            return decodeURIComponent(encoded[1]);
        } catch {
            return encoded[1];
        }
    }
    const plain = header.match(/filename\s*=\s*["']?([^;"']+)["']?/i);
    return plain?.[1] || fallback;
}
