/*
 * «Активность операторов» (задача #392) — отдельный раздел рядом с «Табло ОП». Чистые функции
 * экрана: колонки ТЗ с подсказками, форматы ЧЧ:ММ:СС, фильтры, страницы и подписи журнала.
 * Без React, поэтому проверяются `node --test`.
 *
 * Расчёт — на сервере (op_wallboard/activity.py): месяц приезжает целиком, а группа, операторы
 * и дни фильтруются здесь, мгновенно, без кнопки «Применить». Правило фильтра то же, что у
 * выгрузки на сервере (`activity.filter_shifts`), иначе файл разошёлся бы с таблицей на экране.
 */

export const OP_ACTIVITY_PATH = '/api/op_wallboard/activity';
export const OP_ACTIVITY_JOURNAL_PATH = '/api/op_wallboard/activity/journal';
export const OP_ACTIVITY_EXPORT_PATH = '/api/op_wallboard/activity/export';

export const OP_ACTIVITY_GROUP_ALL = 'all';

// Строк на странице таблицы. Месяц отдела — больше тысячи смен; страница по 50 держит таблицу
// в пару экранов, а листалка внизу говорит, сколько всего и где ты.
export const OP_ACTIVITY_PAGE_SIZE = 50;

const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
    'сентября', 'октября', 'ноября', 'декабря'];

const isBlank = (value) => value === null || value === undefined || value === '';

/** Длительность ЧЧ:ММ:СС, как просит ТЗ; часы не сворачиваются в сутки. Пусто — прочерк. */
export const formatHms = (seconds) => {
    if (isBlank(seconds)) return '—';
    const total = Math.floor(Number(seconds));
    if (!Number.isFinite(total) || total < 0) return '—';
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
};

/** Время из отметки сервера «ГГГГ-ММ-ДДTЧЧ:ММ:СС» как есть — сервер уже отдаёт время Алматы. */
export const formatClockHms = (stamp) => {
    const match = String(stamp || '').match(/[T ](\d{2}:\d{2}:\d{2})/);
    return match ? match[1] : '—';
};

export const formatDayRu = (iso) => {
    const match = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
    return match ? `${match[3]}.${match[2]}.${match[1]}` : '—';
};

export const formatDayShort = (iso) => {
    const match = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
    return match ? `${Number(match[3])} ${MONTHS_GENITIVE[Number(match[2]) - 1]}` : '';
};

export const pluralRu = (n, one, few, many) => {
    const abs = Math.abs(Number(n)) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

/*
 * Колонки — строго в порядке ТЗ. Подсказка «i» — одна фраза, как считается колонка: именно
 * здесь сверяют цифры, и пояснение нужно тогда, когда его ищут, а не строкой под таблицей.
 */
export const OP_ACTIVITY_COLUMNS = [
    { key: 'date', label: 'Дата', hint: 'Дата начала смены: ночная смена целиком относится к вечеру, с которого началась.' },
    { key: 'name', label: 'Оператор' },
    { key: 'group', label: 'Группа', hint: 'Группа, в которой оператор числился в день смены.' },
    { key: 'login', label: 'Вход в систему', hint: 'Первое событие телефона iCORE Phone в смене.' },
    { key: 'avg_talk', label: 'Среднее время разговора', numeric: true,
      hint: 'Общее время разговора, делённое на все попытки — включая сбросы и недозвоны.' },
    { key: 'talk', label: 'Общее время разговора', numeric: true,
      hint: 'Чистый разговор по всем попыткам смены — от соединения до отбоя по данным станции, без набора и ожидания в очереди.' },
    { key: 'active', label: 'Время на исходе / Активный', numeric: true,
      hint: 'Всё время в рабочем статусе: «Активный» у Основы, «Исход» и «Автодозвон» у ЯР и Потока — вместе с наборами и разговорами внутри него.' },
    { key: 'waiting', label: 'Время в ожидании', numeric: true,
      hint: 'Часть предыдущей колонки без звонка: ни набора, ни разговора. Уже входит в неё — складывать их нельзя.' },
    { key: 'break', label: 'Время перерыва', numeric: true, hint: 'Сумма периодов статуса «Перерыв».' },
    { key: 'tech', label: 'Время тех. перерыва', numeric: true, hint: 'Сумма периодов статуса «Технический перерыв».' },
    { key: 'calls', label: 'Количество звонков (все попытки)', numeric: true,
      hint: 'ЯР и Поток — все исходящие попытки, включая короткие и сброшенные. Основа — ещё и входящие, поставленные оператору; сброшенный клиентом во время звонка телефона не в счёт.' },
    { key: 'connected', label: 'Количество дозвонов (успешные разговоры)', numeric: true,
      hint: 'Звонки, где после соединения разговор шёл не меньше 10 секунд.' },
    { key: 'logout', label: 'Время выхода из системы',
      hint: 'Выход из телефона в конце смены. Если телефон выход не прислал, стоит последнее событие смены — серым.' },
    { key: 'worked', label: 'Фактическое отработанное время', numeric: true,
      hint: 'Рабочий статус плюс тренинг, без перерывов, тех. перерывов и офлайна. По телефону за всю смену, без обрезки графиком — поэтому бывает больше, чем в «Учёте часов».' },
];

/** Подсказка дозвона с порогом из ответа сервера — чтобы «10 секунд» не разошлись с расчётом. */
export const opActivityColumns = (talkMinSeconds) => {
    const seconds = Number(talkMinSeconds);
    if (!Number.isFinite(seconds) || seconds === 10) return OP_ACTIVITY_COLUMNS;
    return OP_ACTIVITY_COLUMNS.map((column) => (column.key === 'connected'
        ? { ...column, hint: `Звонки, где после соединения разговор шёл не меньше ${seconds} ${pluralRu(seconds, 'секунды', 'секунд', 'секунд')}.` }
        : column));
};

// Группы ТЗ известны и без ответа сервера: фильтр стоит на месте с первого кадра.
export const OP_ACTIVITY_GROUPS = [
    { key: 'osnova', label: 'Основа' },
    { key: 'yar', label: 'ЯР' },
    { key: 'potok', label: 'Поток' },
];

/** Смены по группам при остальных фильтрах — счётчик на сегменте: видно, куда переключаться. */
export const groupCounts = (shifts, { operatorIds = [], days = [] } = {}) => {
    const counts = { [OP_ACTIVITY_GROUP_ALL]: 0 };
    for (const row of filterShifts(shifts, { group: OP_ACTIVITY_GROUP_ALL, operatorIds, days })) {
        counts[OP_ACTIVITY_GROUP_ALL] += 1;
        counts[row.group_key] = (counts[row.group_key] || 0) + 1;
    }
    return counts;
};

export const groupOptions = (report, counts = null) => [
    { value: OP_ACTIVITY_GROUP_ALL, label: 'Все' },
    ...((report?.groups || OP_ACTIVITY_GROUPS).map((group) => ({ value: group.key, label: group.label }))),
].map((option) => (counts ? { ...option, count: counts[option.value] || 0 } : option));

/** Операторы для фильтра: только те, у кого в месяце есть смены в выбранной группе; справа —
 *  сколько у человека смен в месяце, как число рядом с пунктом меню macOS. */
export const operatorOptions = (report, group) => {
    const shiftsBy = new Map();
    for (const row of report?.shifts || []) {
        if (group && group !== OP_ACTIVITY_GROUP_ALL && row.group_key !== group) continue;
        shiftsBy.set(row.operator_id, (shiftsBy.get(row.operator_id) || 0) + 1);
    }
    return (report?.operators || [])
        .filter((operator) => !group || group === OP_ACTIVITY_GROUP_ALL || (operator.group_keys || []).includes(group))
        .map((operator) => ({
            value: operator.id,
            label: operator.name || `Сотрудник ${operator.id}`,
            meta: shiftsBy.has(operator.id) ? String(shiftsBy.get(operator.id)) : undefined,
        }));
};

/** Выбранные операторы, которых нет в списке группы, из выбора уходят — как в ТЗ. */
export const keepOperators = (report, group, ids) => {
    const allowed = new Set(operatorOptions(report, group).map((option) => option.value));
    return (ids || []).filter((id) => allowed.has(id));
};

export const filterShifts = (shifts, { group = OP_ACTIVITY_GROUP_ALL, operatorIds = [], days = [] } = {}) => {
    const wantedOps = new Set((operatorIds || []).map(Number));
    const wantedDays = new Set(days || []);
    return (shifts || []).filter((row) => (
        (!group || group === OP_ACTIVITY_GROUP_ALL || row.group_key === group)
        && (!wantedOps.size || wantedOps.has(Number(row.operator_id)))
        && (!wantedDays.size || wantedDays.has(row.date))
    ));
};

export const defaultFilters = (report) => ({
    group: OP_ACTIVITY_GROUP_ALL,
    operatorIds: [],
    month: report?.months?.[0]?.value || report?.month || '',
    days: [],
});

/** Отфильтровано ли что-нибудь сверх «весь последний месяц» — тогда и только тогда видно «Сбросить». */
export const isFiltered = (filters, report) => {
    const base = defaultFilters(report);
    return Boolean(filters) && (
        (filters.group && filters.group !== OP_ACTIVITY_GROUP_ALL)
        || (filters.operatorIds || []).length > 0
        || (filters.days || []).length > 0
        || (base.month && filters.month && filters.month !== base.month)
    );
};

/** Соседние месяцы для стрелок: список с сервера идёт от нового к старому. */
export const monthNeighbours = (months, month) => {
    const list = months || [];
    const index = list.findIndex((item) => item.value === month);
    if (index < 0) return { newer: null, older: null };
    return { newer: list[index - 1] || null, older: list[index + 1] || null };
};

/** Подпись месяца без ответа сервера — чтобы шапка фильтров не перестраивалась на первом кадре. */
export const monthLabelOf = (month) => {
    const match = String(month || '').match(/^(\d{4})-(\d{2})$/);
    if (!match) return '';
    const names = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь',
        'Октябрь', 'Ноябрь', 'Декабрь'];
    return `${names[Number(match[2]) - 1]} ${match[1]}`;
};

/** Страница таблицы: номер в пределах, срез строк и подпись «с–по». */
export const pageSlice = (rows, page, size = OP_ACTIVITY_PAGE_SIZE) => {
    const total = (rows || []).length;
    const pageCount = Math.max(1, Math.ceil(total / size));
    const current = Math.min(Math.max(1, Number(page) || 1), pageCount);
    const start = (current - 1) * size;
    return {
        page: current,
        pageCount,
        total,
        from: total ? start + 1 : 0,
        to: Math.min(total, start + size),
        rows: (rows || []).slice(start, start + size),
    };
};

/*
 * Быстрый выбор дней в календаре: вчера и последние семь дней месяца — то, что открывают чаще
 * всего. День вне месяца или в будущем не предлагается: пустой выбор хуже отсутствия кнопки.
 */
export const dayPresets = (month, today) => {
    const { days } = monthGrid(month);
    if (!days.length || !today) return [];
    const ago = (n) => {
        const [y, m, d] = today.split('-').map(Number);
        const date = new Date(y, m - 1, d - n);
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    };
    const inMonth = new Set(days);
    const out = [];
    const yesterday = ago(1);
    if (inMonth.has(yesterday)) out.push({ key: 'yesterday', label: 'Вчера', days: [yesterday] });
    const week = Array.from({ length: 7 }, (_, i) => ago(7 - i)).filter((day) => inMonth.has(day));
    if (week.length > 1) out.push({ key: 'week', label: '7 дней', days: week });
    return out;
};

export const sameFilters = (a, b) => (
    a?.group === b?.group && a?.month === b?.month
    && JSON.stringify([...(a?.operatorIds || [])].sort()) === JSON.stringify([...(b?.operatorIds || [])].sort())
    && JSON.stringify([...(a?.days || [])].sort()) === JSON.stringify([...(b?.days || [])].sort())
);

/** Подпись кнопки «Дни»: весь месяц, один день, несколько. */
export const daysLabel = (days) => {
    const list = [...(days || [])].sort();
    if (!list.length) return 'Весь месяц';
    if (list.length === 1) return formatDayShort(list[0]);
    return `${list.length} ${pluralRu(list.length, 'день', 'дня', 'дней')}`;
};

export const operatorsLabel = (ids, options) => {
    const list = ids || [];
    if (!list.length) return 'Все операторы';
    if (list.length === 1) return (options || []).find((option) => option.value === list[0])?.label || '1 оператор';
    return `${list.length} ${pluralRu(list.length, 'оператор', 'оператора', 'операторов')}`;
};

/** Сетка месяца для выбора дней: пустые клетки до первого числа, дни с понедельника. */
export const monthGrid = (month) => {
    const match = String(month || '').match(/^(\d{4})-(\d{2})$/);
    if (!match) return { lead: 0, days: [] };
    const year = Number(match[1]);
    const index = Number(match[2]) - 1;
    const weekday = new Date(year, index, 1).getDay();
    const total = new Date(year, index + 1, 0).getDate();
    const days = Array.from({ length: total }, (_, i) => `${match[1]}-${match[2]}-${String(i + 1).padStart(2, '0')}`);
    return { lead: weekday === 0 ? 6 : weekday - 1, days };
};

export const summaryLabel = (rows) => {
    const shifts = (rows || []).length;
    const people = new Set((rows || []).map((row) => row.operator_id)).size;
    if (!shifts) return '';
    return `${shifts} ${pluralRu(shifts, 'смена', 'смены', 'смен')} · ${people} ${pluralRu(people, 'оператор', 'оператора', 'операторов')}`;
};

export const pendingCallsNotice = (report) => {
    const count = (report?.calls_pending_days || []).length;
    if (!count) return null;
    return `Звонки за ${count} ${pluralRu(count, 'день', 'дня', 'дней')} ещё догружаются со станции — счётчики звонков обновятся сами`;
};

export const exportQuery = (filters) => {
    const query = new URLSearchParams();
    if (filters?.month) query.set('month', filters.month);
    if (filters?.group && filters.group !== OP_ACTIVITY_GROUP_ALL) query.set('group', filters.group);
    if (filters?.operatorIds?.length) query.set('operator_ids', filters.operatorIds.join(','));
    if (filters?.days?.length) query.set('days', [...filters.days].sort().join(','));
    return query.toString();
};

/* Имя файла собирает фронт: Content-Disposition через CORS сюда не доходит. */
export const exportFileName = (month) => `aktivnost_operatorov_${month || 'month'}.xlsx`;

export const journalQuery = (shift) => {
    const query = new URLSearchParams();
    query.set('operator_id', String(shift?.operator_id ?? ''));
    query.set('start', shift?.login_at || '');
    query.set('end', shift?.logout_at || '');
    return query.toString();
};

/*
 * Журнал: семь статусов ТЗ. Набор номера подписан рабочим статусом, из которого звонили
 * («Активный», «Исход», «Автодозвон»), — те же цвета, что у пилюль на табло, чтобы один
 * смысл на всех экранах горел одинаково. «Ожидание» нейтральное: оно ни плохое, ни хорошее.
 */
export const OP_ACTIVITY_JOURNAL_STYLE = {
    talk: { chip: 'bg-blue-100 text-blue-700', bar: 'bg-blue-500' },
    active: { chip: 'bg-green-100 text-green-700', bar: 'bg-green-500' },
    outbound: { chip: 'bg-amber-100 text-amber-700', bar: 'bg-amber-400' },
    autodial: { chip: 'bg-sky-100 text-sky-700', bar: 'bg-sky-400' },
    wait: { chip: 'bg-slate-100 text-slate-600', bar: 'bg-slate-300' },
    break: { chip: 'bg-orange-100 text-orange-700', bar: 'bg-orange-400' },
    tech: { chip: 'bg-fuchsia-100 text-fuchsia-700', bar: 'bg-fuchsia-400' },
    training: { chip: 'bg-purple-100 text-purple-700', bar: 'bg-purple-400' },
    idle_offline: { chip: 'bg-red-100 text-red-700', bar: 'bg-red-400' },
    logout: { chip: 'bg-slate-50 text-slate-500 ring-1 ring-slate-200', bar: 'bg-slate-200' },
    other: { chip: 'bg-slate-100 text-slate-600', bar: 'bg-slate-300' },
};

export const journalStyle = (style) => OP_ACTIVITY_JOURNAL_STYLE[style] || OP_ACTIVITY_JOURNAL_STYLE.other;

/** Полоса смены: доли отрезков по длительности, соседние одного цвета склеены. */
export const journalBar = (segments) => {
    const parts = [];
    let total = 0;
    for (const segment of segments || []) {
        const seconds = Math.max(0, Number(segment?.seconds) || 0);
        if (!seconds) continue;
        total += seconds;
        const style = segment.style || 'other';
        if (parts.length && parts[parts.length - 1].style === style) parts[parts.length - 1].seconds += seconds;
        else parts.push({ style, seconds });
    }
    return total ? parts.map((part) => ({ ...part, share: part.seconds / total })) : [];
};

/** Итоги журнала по подписям статусов — та же раскладка, что у колонок таблицы. */
export const journalTotals = (segments) => {
    const totals = new Map();
    for (const segment of segments || []) {
        const seconds = Math.max(0, Number(segment?.seconds) || 0);
        if (!seconds) continue;
        const key = segment.kind === 'dial' ? 'dial' : segment.kind;
        totals.set(key, (totals.get(key) || 0) + seconds);
    }
    return totals;
};

/*
 * Фильтр журнала: в смене ЯР бывает шесть сотен отрезков, и перерывы или офлайн в такой ленте
 * не найти глазами. Метки сверху — они же итоги смены по статусам: сколько всего разговора,
 * наборов, ожидания, пауз и офлайна. Метка без времени не показывается.
 */
export const OP_ACTIVITY_JOURNAL_FILTERS = [
    { key: 'all', label: 'Все' },
    { key: 'talk', label: 'Разговоры', kinds: ['talk'] },
    { key: 'dial', label: 'Наборы', kinds: ['dial'] },
    { key: 'wait', label: 'Ожидание', kinds: ['wait'] },
    { key: 'pause', label: 'Паузы', kinds: ['break', 'tech', 'training'] },
    { key: 'offline', label: 'Офлайн', kinds: ['idle_offline', 'logout'] },
];

export const journalFilterChips = (segments) => {
    const sums = {};
    for (const segment of segments || []) {
        sums[segment.kind] = (sums[segment.kind] || 0) + Math.max(0, Number(segment.seconds) || 0);
    }
    return OP_ACTIVITY_JOURNAL_FILTERS
        .map((chip) => ({
            ...chip,
            seconds: chip.kinds ? chip.kinds.reduce((sum, kind) => sum + (sums[kind] || 0), 0) : null,
            present: !chip.kinds || (segments || []).some((segment) => chip.kinds.includes(segment.kind)),
        }))
        .filter((chip) => chip.present);
};

export const filterJournal = (segments, key) => {
    const chip = OP_ACTIVITY_JOURNAL_FILTERS.find((item) => item.key === key);
    if (!chip || !chip.kinds) return segments || [];
    return (segments || []).filter((segment) => chip.kinds.includes(segment.kind));
};
