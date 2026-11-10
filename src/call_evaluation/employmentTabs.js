/*
 * Вкладки статусов в «Аналитике» журнала оценок: кто в «Активных», кто в
 * «Переведённых», кто в «Уволенных».
 *
 * Почему это отдельный файл. Задача #347 (Кастек Гаухар, 20.09.2026): во вкладке
 * «Активные» висели уволенные и переведённые. Уволенного в просматриваемом месяце
 * держало там старое правило «месяц отработан частично — пусть будет среди
 * активных», а ушедшего в другую группу — то, что вкладки «Переведённые» не было
 * вовсе: строка просто получала чип «переведён». Правило теперь покрыто тестами
 * (tests/call_evaluation_employment_tabs.test.mjs), а не живёт внутри 9-тысячного
 * компонента.
 *
 * Правило раскладки, по приоритету:
 *   1. Уволен на конец месяца — «Уволенные», даже если увольнение пришлось на
 *      этот же месяц. Исключение одно: дата увольнения ПОЗЖЕ месяца. Так бывает,
 *      когда в истории нет записи о смене статуса и сервер отдал сегодняшний
 *      статус вместо статуса месяца — в этом месяце человек ещё работал.
 *   2. Ушёл из группы (на последний день месяца его в ней нет) — «Переведённые».
 *   3. Иначе — вкладка его статуса трудоустройства (Активные, Б/С, отпуск...).
 * Пришедший ИЗ другой группы остаётся в «Активных»: он теперь член этой группы.
 *
 * Период вместо месяца (задача #389). «Аналитику» теперь смотрят за любой
 * период — день, неделю, диапазон, год, всё время. Правило то же, только
 * «месяц» в нём — выбранный период: уволен на конец периода, на последний день
 * периода не в этой группе — переведён. Сервер для периода отдаёт сегменты с
 * датами (start_date/end_date): номер дня у периода длиннее месяца ничего не
 * говорит. Контекст — { month } (как было) или { period: { from, to } };
 * пустые края периода — «всё время», его конец — сегодня.
 */

/* Тот же словарь, что в основном приложении. Импортировать оттуда нельзя:
   карты статусов в App.jsx объявлены внутри компонентов. Приводим устаревшие
   имена (unpaid_leave — это Б/С, dismissal — увольнение), иначе один и тот же
   человек попал бы в две вкладки сразу. */
export const EMPLOYMENT_STATUS_ALIASES = {
    unpaid_leave: 'bs',
    dismissal: 'fired',
    dismissed: 'fired',
    terminated: 'fired',
    'уволен': 'fired',
};

export const TRANSFERRED_TAB = 'transferred';

export const EMPLOYMENT_STATUS_META = {
    working: { label: 'Активные', badge: '' },
    bs: { label: 'Б/С', badge: 'Б/С', tone: 'amber' },
    sick_leave: { label: 'Больничный', badge: 'Больничный', tone: 'amber' },
    annual_leave: { label: 'Ежегодный отпуск', badge: 'Отпуск', tone: 'amber' },
    // Перевод — не проблема, а факт: вкладку не красим.
    [TRANSFERRED_TAB]: { label: 'Переведённые', badge: '' },
    fired: { label: 'Уволенные', badge: 'Уволен', tone: 'red' },
};

/* Порядок вкладок фиксированный: «Активные» слева, «Уволенные» справа,
   промежуточные состояния между ними — чтобы вкладки не переставлялись
   от месяца к месяцу. */
export const EMPLOYMENT_STATUS_TAB_ORDER = ['working', 'bs', 'sick_leave', 'annual_leave', TRANSFERRED_TAB, 'fired'];

/* «Активные» и «Уволенные» видны всегда — это опорная пара, и её исчезновение
   читалось бы как сбой. Остальные — только когда в них кто-то есть. */
const ALWAYS_VISIBLE_TABS = new Set(['working', 'fired']);

const normalizeStatus = (status) => String(status ?? '').trim().toLowerCase();

/* Неизвестный статус НЕ сваливаем в «Активные»: если в базе появится новый
   код, он должен получить свою вкладку, а не тихо смешаться с работающими. */
export const normalizeEmploymentStatus = (status) => {
    const s = normalizeStatus(status);
    if (!s) return 'working';
    return EMPLOYMENT_STATUS_ALIASES[s] || s;
};

export const isFiredStatus = (status) => normalizeEmploymentStatus(status) === 'fired';

/* Месяц увольнения ('YYYY-MM') из dismissal_date. null — даты нет. */
export const getDismissalMonth = (op) => {
    const raw = String(op?.dismissal_date || '').trim();
    return /^\d{4}-\d{2}/.test(raw) ? raw.slice(0, 7) : null;
};

export const formatDismissalDate = (op) => {
    const raw = String(op?.dismissal_date || '').trim();
    if (!/^\d{4}-\d{2}-\d{2}/.test(raw)) return null;
    const [y, m, d] = raw.slice(0, 10).split('-');
    return `${d}.${m}.${y}`;
};

const pad2 = (value) => String(value).padStart(2, '0');
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;
const todayIso = () => {
    const now = new Date();
    return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
};
const nextIsoDay = (iso) => {
    const [y, m, d] = iso.split('-').map(Number);
    const date = new Date(y, m - 1, d + 1);
    return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
};

/* Выбранный период вкладок: месяц (как было) или { from, to } (#389).
   Без месяца и без периода — null: правило по датам не применить. */
const resolveContext = (context) => {
    const ctx = typeof context === 'string' ? { month: context } : (context || {});
    const month = parseMonth(ctx.month);
    if (month) return { month };
    const period = ctx.period;
    if (!period || typeof period !== 'object') return null;
    const from = ISO_DAY.test(String(period.from || '')) ? period.from : '';
    const to = ISO_DAY.test(String(period.to || '')) ? period.to : todayIso();
    return { period: { from, to } };
};

const parseMonth = (month) => {
    const match = /^(\d{4})-(\d{2})/.exec(String(month || ''));
    if (!match) return null;
    const year = Number(match[1]);
    const mon = Number(match[2]);
    if (mon < 1 || mon > 12) return null;
    return { year, mon, key: `${match[1]}-${match[2]}`, lastDay: new Date(year, mon, 0).getDate() };
};

/* Сегменты членства внутри месяца (сервер уже обрезал их по границам месяца).
   Членство «конец раньше начала» — перевод, отменённый в момент заведения, — не
   покрывает ни одного дня; сервер такие отсекает, здесь страховка от старого ответа. */
const validSegments = (op) => (Array.isArray(op?.group_segments) ? op.group_segments : [])
    .map((seg) => ({
        groupId: Number(seg?.group_id),
        groupName: String(seg?.group_name || '').trim(),
        startDay: Number(seg?.start_day),
        endDay: Number(seg?.end_day),
        isCurrent: seg?.is_current === true,
    }))
    .filter((seg) => Number.isFinite(seg.groupId)
        && Number.isFinite(seg.startDay)
        && Number.isFinite(seg.endDay)
        && seg.endDay >= seg.startDay);

/* Сегменты членства с датами — для периода (#389). */
const datedSegments = (op) => (Array.isArray(op?.group_segments) ? op.group_segments : [])
    .map((seg) => ({
        groupId: Number(seg?.group_id),
        groupName: String(seg?.group_name || '').trim(),
        start: String(seg?.start_date || ''),
        end: String(seg?.end_date || ''),
        isCurrent: seg?.is_current === true,
    }))
    .filter((seg) => Number.isFinite(seg.groupId)
        && ISO_DAY.test(seg.start) && ISO_DAY.test(seg.end) && seg.end >= seg.start);

/* Перевод за ПЕРИОД — то же правило, что за месяц, только по датам:
   { direction: 'out' | 'in', date, groupName } или { direction: 'shared', groupName }. */
const getGroupTransferInPeriod = (op, gid, period) => {
    const segments = datedSegments(op);
    const here = segments.filter((seg) => seg.groupId === gid);
    if (!here.length) return null;
    const elsewhere = segments.filter((seg) => seg.groupId !== gid);

    const stillHere = here.filter((seg) => seg.isCurrent || seg.end >= period.to);
    if (!stillHere.length) {
        const lastHere = here.map((seg) => seg.end).sort().pop();
        const next = elsewhere
            .filter((seg) => seg.start > lastHere)
            .sort((a, b) => (a.start < b.start ? -1 : 1))[0];
        return { direction: 'out', date: next ? next.start : nextIsoDay(lastHere), groupName: next ? next.groupName : '' };
    }
    if (!elsewhere.length) return null;

    const joined = stillHere.reduce((a, b) => (b.start < a.start ? b : a));
    const previous = elsewhere
        .filter((seg) => seg.end < joined.start)
        .sort((a, b) => (a.end > b.end ? -1 : 1))[0];
    if (previous) return { direction: 'in', date: joined.start, groupName: previous.groupName };
    return { direction: 'shared', groupName: elsewhere[0].groupName };
};

/**
 * Перевод относительно ВЫБРАННОЙ группы за месяц.
 *   { direction: 'out', day, groupName } — ушёл: day — первый день вне группы,
 *       groupName — куда (пусто, если в другую группу не заведён);
 *   { direction: 'in', day, groupName } — пришёл: day — первый день в группе,
 *       groupName — откуда;
 *   { direction: 'shared', groupName } — весь месяц здесь, но числился и в
 *       другой группе (пересечение членств);
 *   null — перевода не было или данных о группах нет (старый ответ).
 */
export function getGroupTransfer(op, groupId, context) {
    const gid = Number(groupId);
    const resolved = resolveContext(context);
    if (!Number.isFinite(gid) || gid <= 0 || !resolved) return null;
    if (resolved.period) return getGroupTransferInPeriod(op, gid, resolved.period);
    const period = resolved.month;

    const segments = validSegments(op);
    const here = segments.filter((seg) => seg.groupId === gid);
    if (!here.length) return null;
    const elsewhere = segments.filter((seg) => seg.groupId !== gid);

    const stillHere = here.filter((seg) => seg.isCurrent || seg.endDay >= period.lastDay);
    if (!stillHere.length) {
        const lastDayHere = Math.max(...here.map((seg) => seg.endDay));
        const next = elsewhere
            .filter((seg) => seg.startDay > lastDayHere)
            .sort((a, b) => a.startDay - b.startDay)[0];
        return {
            direction: 'out',
            day: next ? next.startDay : lastDayHere + 1,
            groupName: next ? next.groupName : '',
        };
    }
    if (!elsewhere.length) return null;

    const joined = stillHere.reduce((a, b) => (b.startDay < a.startDay ? b : a));
    const previous = elsewhere
        .filter((seg) => seg.endDay < joined.startDay)
        .sort((a, b) => b.endDay - a.endDay)[0];
    if (previous) return { direction: 'in', day: joined.startDay, groupName: previous.groupName };
    return { direction: 'shared', groupName: elsewhere[0].groupName };
}

/** Вкладка и эффективный статус строки за месяц (или период) — см. правило в шапке файла. */
export function classifyAnalyticsOperator(op, { month, period, groupId } = {}) {
    const resolved = resolveContext({ month, period });
    let statusKey = normalizeEmploymentStatus(op?.status);
    if (statusKey === 'fired') {
        if (resolved?.month) {
            const firedMonth = getDismissalMonth(op);
            if (firedMonth !== null && firedMonth > resolved.month.key) statusKey = 'working';
        } else if (resolved?.period) {
            // Уволен позже конца периода — статус устарел, в периоде человек ещё работал.
            const firedOn = String(op?.dismissal_date || '').slice(0, 10);
            if (ISO_DAY.test(firedOn) && firedOn > resolved.period.to) statusKey = 'working';
        }
    }
    const transfer = getGroupTransfer(op, groupId, { month, period });
    const tab = statusKey !== 'fired' && transfer?.direction === 'out' ? TRANSFERRED_TAB : statusKey;
    return { tab, statusKey, transfer };
}

/* Раскладка состава ОДНИМ проходом: и счётчики вкладок, и состав каждой из
   них, и разбор строки для чипов — правило в одном месте. */
export function buildEmploymentStatusBuckets(rows, context = {}) {
    const buckets = new Map();
    const infoByOperator = new Map();
    for (const op of (Array.isArray(rows) ? rows : [])) {
        const info = classifyAnalyticsOperator(op, context);
        if (!buckets.has(info.tab)) buckets.set(info.tab, []);
        buckets.get(info.tab).push(op);
        if (op && typeof op === 'object') infoByOperator.set(op, info);
    }
    return { buckets, infoByOperator };
}

export function buildEmploymentStatusTabs(buckets) {
    const countOf = (key) => (buckets.get(key) || []).length;
    const known = EMPLOYMENT_STATUS_TAB_ORDER
        .map((key) => ({
            key,
            label: EMPLOYMENT_STATUS_META[key].label,
            tone: EMPLOYMENT_STATUS_META[key].tone || '',
            count: countOf(key),
        }))
        .filter((tab) => tab.count > 0 || ALWAYS_VISIBLE_TABS.has(tab.key));
    /* Статус, которого нет в словаре (в базе добавили новый код), получает
       вкладку с сырым кодом — лучше непривычная подпись, чем потерянные люди. */
    const unknown = [...buckets.keys()]
        .filter((key) => !EMPLOYMENT_STATUS_META[key])
        .sort((a, b) => String(a).localeCompare(String(b), 'ru'))
        .map((key) => ({ key, label: key, tone: 'amber', count: countOf(key) }));
    // Уволенные всегда последними, новые коды — перед ними.
    const firedTab = known.filter((tab) => tab.key === 'fired');
    return [...known.filter((tab) => tab.key !== 'fired'), ...unknown, ...firedTab];
}

const WHOLE_MONTH_NOTE = 'Оценки и план считаются за месяц целиком, поэтому здесь они показаны полностью, а не только за дни в этой группе.';
const WHOLE_PERIOD_NOTE = 'Оценки считаются за весь выбранный период, поэтому здесь они показаны полностью, а не только за дни в этой группе.';
const shortIso = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
const fullIso = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`;

/**
 * Чип перевода у строки: { text, title } или null.
 * Во вкладке «Переведённые» слово «переведён» было бы дублем заголовка, поэтому
 * чип несёт только дату, а куда перевели — в подсказке. У пришедшего из другой
 * группы — «в группе с …»: он в «Активных», и слово «переведён» там читалось бы
 * как тот самый сбой, из-за которого завели задачу.
 */
export function describeTransferChip(transfer, context) {
    const resolved = resolveContext(context);
    if (!transfer || !resolved) return null;
    let short;
    let full;
    let note = WHOLE_MONTH_NOTE;
    let when = 'В этом месяце';
    let at;
    if (resolved.month) {
        const period = resolved.month;
        const mm = pad2(period.mon);
        short = (day) => `${pad2(day)}.${mm}`;
        full = (day) => `${pad2(day)}.${mm}.${period.year}`;
        at = transfer.day;
    } else {
        // Период (#389): дата перевода — полная, период бывает длиннее месяца.
        if (transfer.direction !== 'shared' && !ISO_DAY.test(String(transfer.date || ''))) return null;
        short = shortIso;
        full = fullIso;
        note = WHOLE_PERIOD_NOTE;
        when = 'В этом периоде';
        at = transfer.date;
    }
    if (transfer.direction === 'out') {
        const where = transfer.groupName
            ? `Переведён(а) в группу «${transfer.groupName}» с ${full(at)}.`
            : `Выведен(а) из группы с ${full(at)}.`;
        return { text: `с ${short(at)}`, title: `${where} ${note}` };
    }
    if (transfer.direction === 'in') {
        const from = transfer.groupName
            ? `Переведён(а) из группы «${transfer.groupName}» ${full(at)}.`
            : `В группе с ${full(at)}.`;
        return { text: `в группе с ${short(at)}`, title: `${from} ${note}` };
    }
    if (transfer.direction === 'shared') {
        const other = transfer.groupName ? `и в группе «${transfer.groupName}»` : 'и в другой группе';
        return { text: 'две группы', title: `${when} числился(ась) ${other}. ${note}` };
    }
    return null;
}

/**
 * Дата увольнения во вкладке «Уволенные» — только если увольнение пришлось на
 * просматриваемый месяц: она объясняет, откуда у уволенного оценки и план. У
 * уволенных давно чип был бы шумом на каждой строке.
 */
export function describeDismissalChip(op, context) {
    const resolved = resolveContext(context);
    const firedOn = formatDismissalDate(op);
    if (!resolved || !firedOn) return null;
    if (resolved.month) {
        if (getDismissalMonth(op) !== resolved.month.key) return null;
    } else {
        // Период (#389): дата — только если увольнение пришлось на выбранные дни.
        const day = String(op?.dismissal_date || '').slice(0, 10);
        if ((resolved.period.from && day < resolved.period.from) || day > resolved.period.to) return null;
    }
    return { text: `с ${firedOn.slice(0, 5)}`, title: `Уволен(а) с ${firedOn}` };
}
