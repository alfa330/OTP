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
export function getGroupTransfer(op, groupId, month) {
    const gid = Number(groupId);
    const period = parseMonth(month);
    if (!Number.isFinite(gid) || gid <= 0 || !period) return null;

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

/** Вкладка и эффективный статус строки за месяц — см. правило в шапке файла. */
export function classifyAnalyticsOperator(op, { month, groupId } = {}) {
    const period = parseMonth(month);
    let statusKey = normalizeEmploymentStatus(op?.status);
    if (statusKey === 'fired') {
        const firedMonth = getDismissalMonth(op);
        if (period && firedMonth !== null && firedMonth > period.key) statusKey = 'working';
    }
    const transfer = getGroupTransfer(op, groupId, month);
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

const pad2 = (value) => String(value).padStart(2, '0');

const WHOLE_MONTH_NOTE = 'Оценки и план считаются за месяц целиком, поэтому здесь они показаны полностью, а не только за дни в этой группе.';

/**
 * Чип перевода у строки: { text, title } или null.
 * Во вкладке «Переведённые» слово «переведён» было бы дублем заголовка, поэтому
 * чип несёт только дату, а куда перевели — в подсказке. У пришедшего из другой
 * группы — «в группе с …»: он в «Активных», и слово «переведён» там читалось бы
 * как тот самый сбой, из-за которого завели задачу.
 */
export function describeTransferChip(transfer, month) {
    const period = parseMonth(month);
    if (!transfer || !period) return null;
    const mm = pad2(period.mon);
    const short = (day) => `${pad2(day)}.${mm}`;
    const full = (day) => `${pad2(day)}.${mm}.${period.year}`;
    if (transfer.direction === 'out') {
        const where = transfer.groupName
            ? `Переведён(а) в группу «${transfer.groupName}» с ${full(transfer.day)}.`
            : `Выведен(а) из группы с ${full(transfer.day)}.`;
        return { text: `с ${short(transfer.day)}`, title: `${where} ${WHOLE_MONTH_NOTE}` };
    }
    if (transfer.direction === 'in') {
        const from = transfer.groupName
            ? `Переведён(а) из группы «${transfer.groupName}» ${full(transfer.day)}.`
            : `В группе с ${full(transfer.day)}.`;
        return { text: `в группе с ${short(transfer.day)}`, title: `${from} ${WHOLE_MONTH_NOTE}` };
    }
    if (transfer.direction === 'shared') {
        const other = transfer.groupName ? `и в группе «${transfer.groupName}»` : 'и в другой группе';
        return { text: 'две группы', title: `В этом месяце числился(ась) ${other}. ${WHOLE_MONTH_NOTE}` };
    }
    return null;
}

/**
 * Дата увольнения во вкладке «Уволенные» — только если увольнение пришлось на
 * просматриваемый месяц: она объясняет, откуда у уволенного оценки и план. У
 * уволенных давно чип был бы шумом на каждой строке.
 */
export function describeDismissalChip(op, month) {
    const period = parseMonth(month);
    const firedOn = formatDismissalDate(op);
    if (!period || !firedOn || getDismissalMonth(op) !== period.key) return null;
    return { text: `с ${firedOn.slice(0, 5)}`, title: `Уволен(а) с ${firedOn}` };
}
