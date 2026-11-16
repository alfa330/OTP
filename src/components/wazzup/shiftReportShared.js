/*
 * «Операторы → Смены» раздела «Чаты ОП»: чистые функции экрана — форматы, колонки с подсказками,
 * фильтры, итоги по людям, сортировка, журнал смены и выгрузка. Без React, поэтому проверяются
 * `node --test` (tests/chat_shift_report.test.mjs).
 *
 * Расчёт — на сервере (wazzup/shift_report.py): период приезжает целиком, а группа, поиск и вид
 * («По сменам / По сотрудникам») отбираются здесь, мгновенно. Средние времени ответа в итогах
 * складываются из сумм строк (firstSum/firstCount, innerSum/innerCount), а не усреднением средних:
 * у человека с одной сменой на два чата и сменой на сорок вес у смен разный.
 */

export const SHIFT_REPORT_PATH = '/api/wazzup/shift_report';

export const GROUP_ALL = 'all';
export const VIEW_SHIFTS = 'shifts';
export const VIEW_PEOPLE = 'people';

export const ROW_SHIFT = 'shift';
export const ROW_NO_SHIFT = 'noShift';
export const ROW_ABSENT = 'absent';

// Тот же порог, что у сервера (shift_report.RATE_MIN_ACTIVE_SECONDS): сервер присылает своё
// значение, это — запас на первый кадр.
export const RATE_MIN_ACTIVE_SECONDS = 15 * 60;
export const DEFAULT_NORMS = { firstReplySeconds: 60, innerReplySeconds: 240 };

const isBlank = (value) => value === null || value === undefined || value === '';
const pad = (n) => String(n).padStart(2, '0');

/* ── период ─────────────────────────────────────────────────────────────── */

export const isoDay = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

const shiftDays = (date, days) => {
    const copy = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    copy.setDate(copy.getDate() + days);
    return copy;
};

export const PERIOD_PRESETS = [
    { key: 'yesterday', label: 'Вчера' },
    { key: 'week', label: '7 дней' },
    { key: 'month', label: '30 дней' },
];

/** Границы быстрого периода от сегодняшней даты. «Вчера» — закончившиеся сутки целиком. */
export const presetRange = (key, today = new Date()) => {
    if (key === 'yesterday') {
        const day = isoDay(shiftDays(today, -1));
        return { from: day, to: day };
    }
    const days = key === 'month' ? 30 : 7;
    return { from: isoDay(shiftDays(today, -(days - 1))), to: isoDay(today) };
};

export const activePreset = (range, today = new Date()) => (
    PERIOD_PRESETS.find((preset) => {
        const expected = presetRange(preset.key, today);
        return expected.from === range?.from && expected.to === range?.to;
    })?.key || null
);

/** Самый ранний день, который ещё можно выбрать: переписка хранится 45 суток. */
export const earliestDay = (today = new Date(), retentionDays = 45) => isoDay(shiftDays(today, -(retentionDays - 1)));

/* ── форматы ────────────────────────────────────────────────────────────── */

/** «ЧЧ:ММ» из отметки сервера «ГГГГ-ММ-ДДTЧЧ:ММ:СС» — сервер уже отдаёт время Алматы. */
export const clock = (stamp) => {
    const match = String(stamp || '').match(/T(\d{2}:\d{2})/);
    return match ? match[1] : '';
};

const dayOf = (stamp) => String(stamp || '').slice(0, 10);

const WEEKDAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

/** «15.11, сб» — день недели помогает читать график: выходные смены видны сразу. */
export const formatDay = (iso) => {
    const match = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!match) return '—';
    const weekday = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))).getUTCDay();
    return `${match[3]}.${match[2]}, ${WEEKDAYS[weekday]}`;
};

/** Длительность статуса: «7 ч 34 м», «45 м», «< 1 м»; ноль — прочерк. */
export const formatDuration = (seconds) => {
    const total = Math.round(Number(seconds) || 0);
    if (total <= 0) return '—';
    if (total < 60) return '< 1 м';
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (!hours) return `${minutes} м`;
    return minutes ? `${hours} ч ${minutes} м` : `${hours} ч`;
};

/** Время ответа — как в «Аналитике» раздела: «45 с», «1 м 20 с», «2 ч 15 м». */
export const formatReply = (seconds) => {
    if (isBlank(seconds) || !Number.isFinite(Number(seconds))) return '—';
    const total = Math.round(Number(seconds));
    if (total < 60) return `${total} с`;
    if (total < 3600) {
        const minutes = Math.floor(total / 60);
        return total % 60 ? `${minutes} м ${total % 60} с` : `${minutes} м`;
    }
    const hours = Math.floor(total / 3600);
    const minutes = Math.round((total % 3600) / 60);
    return minutes ? `${hours} ч ${minutes} м` : `${hours} ч`;
};

export const formatRate = (value) => (
    isBlank(value) || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(1).replace('.', ','));

/** «ЧЧ:ММ:СС» для выгрузки: Excel читает это как время, часы не сворачиваются в сутки. */
export const formatHms = (seconds) => {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    return `${Math.floor(total / 3600)}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
};

export const average = (sum, count) => (count ? Number(sum) / Number(count) : null);

export const chatsPerHour = (chats, activeSeconds, minActive = RATE_MIN_ACTIVE_SECONDS) => (
    !activeSeconds || activeSeconds < minActive ? null : Math.round((chats / (activeSeconds / 3600)) * 10) / 10);

export const firstReply = (row) => average(row?.firstSum, row?.firstCount);
export const innerReply = (row) => average(row?.innerSum, row?.innerCount);

/** Дольше нормы — единственный повод покрасить время ответа. */
export const overNorm = (seconds, norm) => !isBlank(seconds) && Number.isFinite(norm) && seconds > norm;

const PLAN_TYPES = { phone_shift: 'телефон', office_practice: 'офис' };

/** «09:00–18:00», ночная — «21:00–09:00», вид не «чатовой» смены — в скобках. */
export const planLabel = (plan) => (plan || [])
    .map((item) => `${item.start}–${item.end}${PLAN_TYPES[item.type] ? ` (${PLAN_TYPES[item.type]})` : ''}`)
    .join(', ');

/** Начало и конец смены для таблицы; конец на следующие сутки помечен. */
export const spanOf = (row) => ({
    start: clock(row?.start),
    end: row?.open ? 'сейчас' : clock(row?.end),
    nextDay: Boolean(row?.end && !row.open && dayOf(row.end) !== row.date),
});

const END_NOTES = {
    portal: 'портал закрыт',
    long: 'смену не закрыли',
};

/** Пометка конца смены: закрыл её не сам человек, а раздел. */
export const endNote = (row) => END_NOTES[row?.endNote] || '';

/** «вне раздела 56 м» — посреди смены портал был закрыт; меньше минуты — не повод писать. */
export const offlineNote = (row) => (
    row?.kind === ROW_SHIFT && Number(row.offline) >= 60 ? `вне раздела ${formatDuration(row.offline)}` : '');

export const minutesLabel = (seconds) => {
    const minutes = Math.round(Number(seconds || 0) / 60);
    if (minutes < 60) return `${minutes} мин`;
    const hours = Math.floor(minutes / 60);
    return minutes % 60 ? `${hours} ч ${minutes % 60} мин` : `${hours} ч`;
};

/* ── колонки ────────────────────────────────────────────────────────────── */

const plural = (n, one, few, many) => {
    const abs = Math.abs(Number(n)) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

/** Норма словами: «1 минута», «4 минуты»; некруглая — как время ответа. */
export const normText = (seconds) => {
    const total = Math.round(Number(seconds) || 0);
    if (total > 0 && total % 60 === 0 && total < 3600) {
        const minutes = total / 60;
        return `${minutes} ${plural(minutes, 'минута', 'минуты', 'минут')}`;
    }
    return formatReply(total);
};

/*
 * Подсказка «i» — одна-две фразы о том, как считается колонка: здесь сверяют цифры, и пояснение
 * нужно, когда его ищут, а не строкой под таблицей. Нормы ответа — с сервера (те же, что у табло).
 */
export const shiftColumns = (norms = DEFAULT_NORMS) => [
    { key: 'date', label: 'Дата', hint: 'Дата начала смены: ночная смена целиком относится к вечеру, с которого началась.' },
    { key: 'name', label: 'Менеджер' },
    { key: 'plan', label: 'График', hint: 'Смена из «Графиков работы», которая пересекается со сменой в разделе. «Вне графика» — такой смены по графику не было.' },
    { key: 'shift', label: 'Смена', hint: 'Начало и конец смены в «Чатах ОП» по кнопке смены. Опоздание и ранний уход — те же, что в «Учёте часов». «Портал закрыт» — смену закрыл раздел: портал закрыли, не нажав «Закончить смену». «Вне раздела» — посреди смены портал закрыли и открыли снова меньше чем через 4 часа: это время не работа и не перерыв.' },
    { key: 'active', label: 'Активный', numeric: true, hint: 'Время в статусе «Активный» — только в нём можно писать клиентам.' },
    { key: 'breakTime', label: 'Перерыв', numeric: true, hint: 'Время в статусе «Перерыв».' },
    { key: 'training', label: 'Тренинг', numeric: true, hint: '«Тренинг» вместе с «Тех. паузой»; по отдельности — в журнале смены.' },
    { key: 'chats', label: 'Чаты', numeric: true, hint: 'Разные чаты, где он писал клиенту в эту смену. Под числом — сколько он отправил сообщений.' },
    { key: 'chatsPerHour', label: 'Чатов в час', numeric: true, hint: 'Чаты ÷ часы в «Активном». Меньше 15 минут «Активного» — не считается.' },
    { key: 'firstReply', label: 'Первый ответ', numeric: true, hint: `Среднее время первого ответа в диалогах, где он ответил клиенту первым. Норма — ${normText(norms.firstReplySeconds)}, как на «Табло ОП · Чат».` },
    { key: 'innerReply', label: 'Ответ в чате', numeric: true, hint: `Среднее время его ответов внутри диалога: сначала среднее в каждом диалоге, потом по диалогам. Норма — ${normText(norms.innerReplySeconds)}.` },
];

export const peopleColumns = (norms = DEFAULT_NORMS) => [
    { key: 'name', label: 'Менеджер' },
    { key: 'shifts', label: 'Смены', numeric: true, hint: 'Смены в разделе за период. Ниже — дни, когда писал без смены, и смены по графику, на которые не вышел.' },
    { key: 'active', label: 'Активный', numeric: true, hint: 'Время в статусе «Активный» за все смены периода.' },
    { key: 'breakTime', label: 'Перерыв', numeric: true, hint: 'Время в статусе «Перерыв» за период.' },
    { key: 'training', label: 'Тренинг', numeric: true, hint: '«Тренинг» вместе с «Тех. паузой» за период.' },
    { key: 'chats', label: 'Чаты', numeric: true, hint: 'Сумма по сменам и дням без смены: чат, который он вёл в двух сменах, считается в каждой — как «Диалоги» в «Аналитике». Под числом — его сообщения за период.' },
    { key: 'chatsPerHour', label: 'Чатов в час', numeric: true, hint: 'Чаты в сменах ÷ часы в «Активном» за период. Чаты без смены сюда не входят — для них нет часов.' },
    { key: 'firstReply', label: 'Первый ответ', numeric: true, hint: `Среднее по всем диалогам периода, где он ответил первым. Норма — ${normText(norms.firstReplySeconds)}.` },
    { key: 'innerReply', label: 'Ответ в чате', numeric: true, hint: `Среднее его ответов по всем диалогам периода. Норма — ${normText(norms.innerReplySeconds)}.` },
];

/* ── группы, фильтры, люди ─────────────────────────────────────────────── */

/** Группы для переключателя с числом строк при текущем поиске: видно, куда переключаться. */
export const groupOptions = (groups, rows, query = '') => {
    const shown = filterRows(rows, { group: GROUP_ALL, query });
    const counts = new Map();
    for (const row of shown) counts.set(row.groupKey, (counts.get(row.groupKey) || 0) + 1);
    return [
        { value: GROUP_ALL, label: 'Все', count: shown.length },
        ...(groups || []).map((group) => ({ value: group.key, label: group.label, count: counts.get(group.key) || 0 })),
    ];
};

export const filterRows = (rows, { group = GROUP_ALL, query = '' } = {}) => {
    const needle = String(query || '').trim().toLowerCase();
    return (rows || []).filter((row) => (
        (!group || group === GROUP_ALL || row.groupKey === group)
        && (!needle || String(row.name || '').toLowerCase().includes(needle))
    ));
};

const SUM_FIELDS = ['active', 'breakTime', 'training', 'tech', 'offline', 'chats', 'messages',
    'firstSum', 'firstCount', 'innerSum', 'innerCount'];

const emptySums = () => Object.fromEntries(SUM_FIELDS.map((field) => [field, 0]));

/** Сумма строк: длительности, чаты, суммы ответа и счётчики видов строк. */
export const sumRows = (rows, minActive = RATE_MIN_ACTIVE_SECONDS) => {
    const total = { ...emptySums(), shifts: 0, noShiftDays: 0, absent: 0, shiftChats: 0, shiftActive: 0 };
    for (const row of rows || []) {
        if (row.kind === ROW_ABSENT) {
            total.absent += row.absent ?? 1;
            continue;
        }
        for (const field of SUM_FIELDS) total[field] += Number(row[field]) || 0;
        if (row.kind === ROW_SHIFT) {
            total.shifts += 1;
            total.shiftChats += Number(row.chats) || 0;
        } else if (row.kind === ROW_NO_SHIFT) {
            total.noShiftDays += 1;
        } else {
            // строка человека из peopleRows — уже сумма
            total.shifts += row.shifts || 0;
            total.noShiftDays += row.noShiftDays || 0;
            total.absent += row.absent || 0;
            total.shiftChats += row.shiftChats || 0;
        }
    }
    total.chatsPerHour = chatsPerHour(total.shiftChats, total.active, minActive);
    return total;
};

/** Строки «По сотрудникам»: всё, что человек сделал за период, одной строкой. Группа — с его
 *  самой свежей строки (строки приходят от нового дня к старому). */
export const peopleRows = (rows, minActive = RATE_MIN_ACTIVE_SECONDS) => {
    const byUser = new Map();
    for (const row of rows || []) {
        if (!byUser.has(row.userId)) byUser.set(row.userId, []);
        byUser.get(row.userId).push(row);
    }
    return [...byUser.entries()].map(([userId, own]) => {
        const latest = own.reduce((best, row) => (row.date > best.date ? row : best), own[0]);
        const total = sumRows(own, minActive);
        return {
            ...total,
            key: `person:${userId}`,
            kind: 'person',
            userId,
            name: latest.name,
            groupKey: latest.groupKey,
            groupLabel: latest.groupLabel,
        };
    });
};

/* ── сортировка ─────────────────────────────────────────────────────────── */

const SORT_VALUE = {
    date: (row) => row.date,
    name: (row) => String(row.name || ''),
    plan: (row) => (row.plan?.length ? row.plan[0].start : null),
    shift: (row) => (row.kind === ROW_SHIFT ? row.start : null),
    training: (row) => (Number(row.training) || 0) + (Number(row.tech) || 0),
    firstReply,
    innerReply,
    chatsPerHour: (row) => (isBlank(row.chatsPerHour) ? null : row.chatsPerHour),
};

const valueOf = (row, key) => (SORT_VALUE[key] ? SORT_VALUE[key](row) : row[key]);

/** Сортировка таблицы: пустые значения всегда внизу; по дате — свежие сверху, внутри дня по
 *  времени начала, как приходят с сервера. */
export const sortRows = (rows, { key, dir }) => {
    const mul = dir === 'asc' ? 1 : -1;
    return [...(rows || [])].sort((a, b) => {
        const av = valueOf(a, key);
        const bv = valueOf(b, key);
        if (isBlank(av) || isBlank(bv)) {
            if (isBlank(av) && !isBlank(bv)) return 1;
            if (!isBlank(av) && isBlank(bv)) return -1;
        } else if (av !== bv) {
            if (typeof av === 'string') return av.localeCompare(bv, 'ru') * mul;
            return (av - bv) * mul;
        }
        if (key === 'date') {
            const byStart = String(a.start || '').localeCompare(String(b.start || ''));
            if (byStart) return byStart;
        }
        return String(a.name || '').localeCompare(String(b.name || ''), 'ru');
    });
};

export const DEFAULT_SORT = {
    [VIEW_SHIFTS]: { key: 'date', dir: 'desc' },
    [VIEW_PEOPLE]: { key: 'chats', dir: 'desc' },
};

/** Направление по умолчанию при выборе колонки: имена — по алфавиту, числа — от больших. */
export const nextSort = (current, key) => (
    current?.key === key
        ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'name' || key === 'plan' || key === 'shift' ? 'asc' : 'desc' });

/* ── журнал смены ──────────────────────────────────────────────────────── */

export const SEGMENT_LABELS = {
    active: 'Активный',
    break: 'Перерыв',
    training: 'Тренинг',
    tech: 'Тех. пауза',
    offline: 'Не в разделе',
};

// Тона — те же, что у статуса смены в шапке раздела (workspaceStatus.js).
export const SEGMENT_TONES = {
    active: 'work',
    break: 'break',
    training: 'training',
    tech: 'tech',
    offline: 'off',
};

const SEGMENT_ORDER = ['active', 'break', 'training', 'tech', 'offline'];

const OFFLINE_NOTES = {
    portal: 'портал закрыли — смену закрыл раздел',
    long: 'статус не менялся 16 часов',
};

const secondsBetween = (start, end) => {
    const a = Date.parse(`${start}Z`);
    const b = Date.parse(`${end}Z`);
    return Number.isFinite(a) && Number.isFinite(b) ? Math.max(0, Math.round((b - a) / 1000)) : 0;
};

/** Отрезки журнала: вид, подпись, длительность, «с — по» и пометка, почему портала не было. */
export const journalSegments = (row) => (row?.segments || []).map((segment) => ({
    kind: segment.kind,
    tone: SEGMENT_TONES[segment.kind] || 'other',
    label: SEGMENT_LABELS[segment.kind] || segment.kind,
    seconds: secondsBetween(segment.start, segment.end),
    start: clock(segment.start),
    end: clock(segment.end),
    nextDay: dayOf(segment.end) !== dayOf(segment.start),
    note: segment.kind === 'offline'
        ? (OFFLINE_NOTES[segment.note] || 'смену закончили и начали снова')
        : '',
}));

/** Полоса смены: доля каждого отрезка подряд, как он шёл. */
export const journalBar = (row) => {
    const segments = journalSegments(row);
    const total = segments.reduce((sum, segment) => sum + segment.seconds, 0);
    if (!total) return [];
    return segments.filter((segment) => segment.seconds > 0)
        .map((segment) => ({ kind: segment.kind, tone: segment.tone, share: segment.seconds / total }));
};

/** Итоги по видам — метки над лентой, в постоянном порядке. */
export const journalTotals = (row) => {
    const totals = new Map();
    for (const segment of journalSegments(row)) {
        totals.set(segment.kind, (totals.get(segment.kind) || 0) + segment.seconds);
    }
    return SEGMENT_ORDER.filter((kind) => totals.get(kind) > 0)
        .map((kind) => ({ kind, tone: SEGMENT_TONES[kind], label: SEGMENT_LABELS[kind], seconds: totals.get(kind) }));
};

/** Часы смены для графика нагрузки: чаты и сообщения по часам, высота — от самого занятого часа. */
export const hourBars = (row) => {
    const hours = row?.hours || [];
    const max = Math.max(1, ...hours.map((hour) => Number(hour.chats) || 0));
    return hours.map((hour) => ({
        key: hour.hour,
        label: String(hour.hour || '').slice(11, 13),
        chats: Number(hour.chats) || 0,
        messages: Number(hour.messages) || 0,
        share: (Number(hour.chats) || 0) / max,
    }));
};

/* ── выгрузка ───────────────────────────────────────────────────────────── */

const KIND_LABELS = { [ROW_SHIFT]: 'Смена', [ROW_NO_SHIFT]: 'Без смены', [ROW_ABSENT]: 'Не вышел' };

const replySeconds = (value) => (isBlank(value) ? '' : Math.round(value));

/** Строки CSV вида «По сменам» — те же, что на экране, со всеми полями. */
export const shiftCsv = (rows) => [
    ['Дата', 'Менеджер', 'Группа', 'Строка', 'График', 'Начало', 'Конец', 'Опоздание, мин',
        'Ранний уход, мин', 'Активный', 'Перерыв', 'Тренинг', 'Тех. пауза', 'Не в разделе', 'Чаты',
        'Сообщения', 'Чатов в час', 'Первых ответов', 'Первый ответ, с', 'Ответ в чате, с'],
    ...(rows || []).map((row) => {
        const span = spanOf(row);
        const worked = row.kind !== ROW_ABSENT;
        return [
            row.date, row.name, row.groupLabel, KIND_LABELS[row.kind] || '', planLabel(row.plan),
            row.kind === ROW_ABSENT ? '' : span.start,
            row.kind === ROW_ABSENT ? '' : `${span.end}${span.nextDay ? ' (+1)' : ''}`,
            row.lateSeconds ? Math.round(row.lateSeconds / 60) : '',
            row.earlySeconds ? Math.round(row.earlySeconds / 60) : '',
            row.kind === ROW_SHIFT ? formatHms(row.active) : '',
            row.kind === ROW_SHIFT ? formatHms(row.breakTime) : '',
            row.kind === ROW_SHIFT ? formatHms(row.training) : '',
            row.kind === ROW_SHIFT ? formatHms(row.tech) : '',
            row.kind === ROW_SHIFT ? formatHms(row.offline) : '',
            worked ? row.chats : '', worked ? row.messages : '',
            isBlank(row.chatsPerHour) ? '' : formatRate(row.chatsPerHour),
            worked ? row.firstCount : '', replySeconds(firstReply(row)), replySeconds(innerReply(row)),
        ];
    }),
];

/** Строки CSV вида «По сотрудникам». */
export const peopleCsv = (rows) => [
    ['Менеджер', 'Группа', 'Смен', 'Дней без смены', 'Не вышел', 'Активный', 'Перерыв', 'Тренинг',
        'Тех. пауза', 'Не в разделе', 'Чаты', 'Сообщения', 'Чатов в час', 'Первых ответов',
        'Первый ответ, с', 'Ответ в чате, с'],
    ...(rows || []).map((row) => [
        row.name, row.groupLabel, row.shifts, row.noShiftDays, row.absent,
        formatHms(row.active), formatHms(row.breakTime), formatHms(row.training), formatHms(row.tech),
        formatHms(row.offline), row.chats, row.messages,
        isBlank(row.chatsPerHour) ? '' : formatRate(row.chatsPerHour),
        row.firstCount, replySeconds(firstReply(row)), replySeconds(innerReply(row)),
    ]),
];

/** Имя файла — ASCII: не все корпоративные почтовики и диски переживают кириллицу. */
export const csvFileName = (view, range) => `chat_shifts_${view === VIEW_PEOPLE ? 'people' : 'shifts'}_${range?.from || ''}_${range?.to || ''}.csv`;
