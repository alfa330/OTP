import axios from 'axios';

/*
 * Заявки на отпуск, Б/С и больничный (задачи #358, #346) — общие правила
 * экрана, без React.
 *
 * Оператор подаёт заявку тем же «Новым запросом», что и заявки по сменам
 * (пункт «Отпуск, Б/С или больничный»), СВ и глава разбирают её во вкладке
 * очереди «Запросы». Правила маршрута живут на сервере (leave_requests/rules.py)
 * — здесь только подписи, счёт дней и проверка формы ради подсказки, а не
 * вместо права.
 *
 * Какие виды принимает отдел, решает его настройка, поэтому подписи собираются
 * из списка видов (leaveKindsText), а не написаны одной строкой: у отдела без
 * больничного слова «больничный» в окне быть не должно.
 */

// Значение вида в общем списке «Что нужно» окна заявки. Это не код заявки, а
// пункт списка: конкретный вид выбирается уже в форме.
export const LEAVE_REQUEST_OPTION = 'leave';

export const LEAVE_KINDS = ['annual_leave', 'bs', 'sick_leave'];

// Коды — те же, что у статусов графика: утверждённая заявка ставит ровно такой период.
export const LEAVE_KIND_LABELS = {
    annual_leave: 'Ежегодный отпуск',
    bs: 'Б/С',
    sick_leave: 'Больничный',
};

// Подписи сегментов формы: два-три варианта — сегменты, все названы словами.
export const LEAVE_KIND_SEGMENTS = {
    annual_leave: 'Отпуск',
    bs: 'Б/С',
    sick_leave: 'Больничный',
};

// Больничный подают и задним числом — до стольких дней назад
// (leave_requests/rules.py: SICK_BACK_DAYS; сервер присылает своё значение).
export const LEAVE_SICK_BACK_DAYS = 30;

const KIND_HINT_LINES = {
    annual_leave: 'Отпуск — ежегодный оплачиваемый отпуск.',
    bs: 'Б/С — отпуск без сохранения заработной платы.',
    sick_leave: `Больничный — если заболели. Можно подать и задним числом, до ${LEAVE_SICK_BACK_DAYS} дней назад.`,
};

const KIND_WORDS = { annual_leave: 'отпуск', bs: 'Б/С', sick_leave: 'больничный' };

// Виды в порядке LEAVE_KINDS; пустой список — все: подпись без видов не бывает.
const pickKinds = (kinds) => {
    const list = LEAVE_KINDS.filter((kind) => (kinds || []).includes(kind));
    return list.length ? list : LEAVE_KINDS;
};

// Слова через запятую; `last` — «и»/«или» перед последним, null — только запятые.
const joinKinds = (list, last) => {
    const words = list.map((kind) => KIND_WORDS[kind]);
    if (words.length === 1 || !last) return words.join(', ');
    return `${words.slice(0, -1).join(', ')} ${last} ${words[words.length - 1]}`;
};

const upperFirst = (text) => text.charAt(0).toUpperCase() + text.slice(1);

/** «отпуск, Б/С и больничный» — виды словами; `last` — «и» или «или». */
export const leaveKindsText = (kinds, last = 'и') => joinKinds(pickKinds(kinds), last);

export const leaveKindsTitle = (kinds, last = 'и') => upperFirst(leaveKindsText(kinds, last));

/* Короткие подписи — для сегмента и строки списка на телефоне: строку они не
   переносят, а «Отпуск, Б/С и больничный» шире половины экрана 390 px (164 px
   текста при 152 px места). Когда видов три, Б/С — тоже отпуск, без сохранения
   зарплаты, — входит в «отпуск». */
const shortKinds = (kinds) => {
    const list = pickKinds(kinds);
    return list.length === LEAVE_KINDS.length ? list.filter((kind) => kind !== 'bs') : list;
};

/** «Отпуск и больничный» — вкладка очереди на телефоне. */
export const leaveKindsShortTitle = (kinds) => upperFirst(joinKinds(shortKinds(kinds), 'и'));

/** «отпуск, больничный» — перечень в подписи строки на телефоне. */
export const leaveKindsShortList = (kinds) => joinKinds(shortKinds(kinds), null);

/** Пояснение к выбору вида под «i» — ровно по строке на вариант формы. */
export const leaveKindHints = (kinds) => pickKinds(kinds).map((kind) => KIND_HINT_LINES[kind]);

const SINGLE_OPTION = { annual_leave: 'Взять отпуск', bs: 'Взять Б/С', sick_leave: 'Больничный' };
const SINGLE_TITLE = { annual_leave: 'Заявка на отпуск', bs: 'Заявка на Б/С', sick_leave: 'Заявка на больничный' };

export const leaveOptionLabel = (kinds = []) => (
    kinds.length === 1 && SINGLE_OPTION[kinds[0]] ? SINGLE_OPTION[kinds[0]] : leaveKindsTitle(kinds, 'или')
);

export const leaveModalTitle = (draft, kinds = []) => {
    if (draft?.id) return 'Изменить заявку';
    if (kinds.length === 1 && SINGLE_TITLE[kinds[0]]) return SINGLE_TITLE[kinds[0]];
    return leaveKindsTitle(kinds, 'или');
};

// Строка «i» у списка «Что нужно» окна заявки.
export const leaveOptionHint = (kinds) => `${leaveKindsTitle(kinds, 'или')} — на период: сначала согласует супервайзер, затем утверждает руководитель отдела, и период сам встаёт в график.`;

/** Самое раннее начало: больничный — задним числом, остальное — с сегодня. */
export const leaveEarliestStart = (kind, today, sickBackDays = LEAVE_SICK_BACK_DAYS) => (
    kind === 'sick_leave' && today ? addDaysIso(today, -sickBackDays) : today
);

/* Статусы заявки. Цвет — только со смыслом: жёлтым то, что ещё в пути, синим
   то, что ждёт ответа самого оператора, зелёным и красным — итог. */
export const LEAVE_STATUS_META = {
    supervisor: { label: 'Ждёт супервайзера', short: 'У супервайзера', tone: 'amber' },
    head: { label: 'Ждёт руководителя', short: 'У руководителя', tone: 'amber' },
    proposal: { label: 'Предложены другие даты', short: 'Другие даты', tone: 'blue' },
    approved: { label: 'Утверждена', short: 'Утверждена', tone: 'green' },
    rejected: { label: 'Отклонена', short: 'Отклонена', tone: 'red' },
    cancelled: { label: 'Отозвана', short: 'Отозвана', tone: 'slate' },
};

export const LEAVE_OPEN_STATUSES = ['supervisor', 'head', 'proposal'];

// Журнал заявки — короткими глаголами: строка истории, а не пересказ.
export const LEAVE_EVENT_LABELS = {
    created: 'Подана',
    edited: 'Изменена',
    cancelled: 'Отозвана',
    supervisor_approved: 'Согласовал супервайзер',
    proposed: 'Предложены другие даты',
    proposal_accepted: 'Оператор согласился с датами',
    proposal_declined: 'Оператор не согласен с датами',
    approved: 'Утверждена',
    rejected: 'Отклонена',
};

// ── Даты ────────────────────────────────────────────────────────────────────

const DAY_MS = 24 * 60 * 60 * 1000;

const parseDay = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
    if (!match) return null;
    return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
};

export const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);

export const addDaysIso = (value, days) => {
    const ms = parseDay(value);
    return ms === null ? '' : isoDay(ms + days * DAY_MS);
};

/** Календарных дней в периоде, оба края включительно (ТЗ п. 1). */
export const leavePeriodDays = (start, end) => {
    const a = parseDay(start);
    const b = parseDay(end);
    if (a === null || b === null || b < a) return 0;
    return Math.round((b - a) / DAY_MS) + 1;
};

export const daysWord = (count) => {
    const n = Math.abs(Number(count) || 0);
    if (n % 10 === 1 && n % 100 !== 11) return 'день';
    if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return 'дня';
    return 'дней';
};

export const daysText = (count) => `${count} ${daysWord(count)}`;

/** «01.12 — 14.12»; год — только когда он не текущий. */
export const formatLeavePeriod = (start, end, today = '') => {
    const year = String(today || isoDay(Date.now())).slice(0, 4);
    const one = (value) => {
        const [y, m, d] = String(value || '').slice(0, 10).split('-');
        if (!y || !m || !d) return '';
        return y === year ? `${d}.${m}` : `${d}.${m}.${y}`;
    };
    if (!start) return '';
    if (!end || end === start) return one(start);
    return `${one(start)} — ${one(end)}`;
};

/** «02.11 14:05» — время шага в истории заявки; год — только когда он не текущий. */
export const formatLeaveDateTime = (iso, today = '') => {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ''));
    if (!match) return '';
    const year = String(today || isoDay(Date.now())).slice(0, 4);
    return `${match[3]}.${match[2]}${match[1] === year ? '' : `.${match[1]}`} ${match[4]}:${match[5]}`;
};

/** «Ежегодный отпуск · 01.12 — 14.12 · 14 дней». */
export const leaveSummary = (item, today = '') => [
    LEAVE_KIND_LABELS[item?.kind] || 'Заявка',
    formatLeavePeriod(item?.startDate, item?.endDate, today),
    daysText(leavePeriodDays(item?.startDate, item?.endDate)),
].filter(Boolean).join(' · ');

// ── Журнал ──────────────────────────────────────────────────────────────────

// Отбор по статусу — те же значения, что у сервера (rules.JOURNAL_STATUSES).
export const LEAVE_JOURNAL_STATUSES = [
    { value: '', label: 'Все статусы' },
    { value: 'open', label: 'На рассмотрении' },
    { value: 'approved', label: 'Утверждены' },
    { value: 'rejected', label: 'Отклонены' },
    { value: 'cancelled', label: 'Отозваны' },
];

export const leaveJournalKinds = (kinds) => [
    { value: '', label: 'Все виды' },
    ...pickKinds(kinds).map((kind) => ({ value: kind, label: LEAVE_KIND_LABELS[kind] })),
];

const MONTH_NAMES = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь',
    'Октябрь', 'Ноябрь', 'Декабрь'];

/** '2026-11' → 'Ноябрь 2026'. */
export const leaveMonthLabel = (month) => {
    const [year, number] = String(month || '').split('-').map(Number);
    return year && number >= 1 && number <= 12 ? `${MONTH_NAMES[number - 1]} ${year}` : '';
};

/** Соседний месяц: ('2026-12', 1) → '2027-01'. */
export const shiftLeaveMonth = (month, delta) => {
    const [year, number] = String(month || '').split('-').map(Number);
    if (!year || !number) return month;
    return isoDay(Date.UTC(year, number - 1 + delta, 1)).slice(0, 7);
};

// Пустой вид или статус — «все»: в запрос не идут.
export const leaveJournalQuery = ({ month, kind, status }) => ({
    month,
    ...(kind ? { kind } : {}),
    ...(status ? { status } : {}),
});

export const leaveJournalFileName = (month) => `Журнал заявок ${month}.xlsx`;

/* Файл — в этом же окне, временной ссылкой (правило портала: без новых
   вкладок). Ссылка на blob, а не startFileDownload: выгрузку собирает наш API
   за авторизацией, скрытый фрейм без заголовка получил бы отказ. */
export const saveLeaveFile = (blob, name, doc = globalThis.document) => {
    if (!doc?.body || typeof URL === 'undefined' || !URL.createObjectURL) return false;
    const url = URL.createObjectURL(blob);
    const link = doc.createElement('a');
    link.href = url;
    link.download = name;
    doc.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return true;
};

// ── Форма ───────────────────────────────────────────────────────────────────

// Даты пустые: у отпуска нет «естественного» дня по умолчанию, и подставленный
// завтрашний легко отправить, не заметив.
export const emptyLeaveDraft = (kinds = []) => ({
    id: null,
    kind: kinds.length === 1 ? kinds[0] : null,
    startDate: '',
    endDate: '',
    reason: '',
});

export const draftFromRequest = (item) => ({
    id: item?.id ?? null,
    kind: item?.kind || null,
    startDate: item?.startDate || '',
    endDate: item?.endDate || '',
    reason: item?.reason || '',
});

/**
 * Чего не хватает форме — одной строкой (так же, как у заявки по смене:
 * «одна строка на всё»). Пусто — можно отправлять. Сервер проверит заново.
 */
export const leaveDraftProblem = (draft, {
    today = '', kinds = [], maxPeriodDays = 365, maxAheadDays = 365, sickBackDays = LEAVE_SICK_BACK_DAYS,
} = {}) => {
    if (!draft) return 'Заполните заявку';
    if (!draft.kind || (kinds.length && !kinds.includes(draft.kind) && !draft.id)) return `Выберите: ${leaveKindsText(kinds, 'или')}`;
    if (!draft.startDate) return 'Выберите первый день';
    if (!draft.endDate) return 'Выберите последний день';
    if (today && draft.startDate < leaveEarliestStart(draft.kind, today, sickBackDays)) {
        return draft.kind === 'sick_leave'
            ? `Больничный можно подать задним числом не больше чем на ${sickBackDays} дней`
            : 'Начало уже прошло — выберите сегодняшний день или позже';
    }
    if (draft.endDate < draft.startDate) return 'Последний день раньше первого';
    if (leavePeriodDays(draft.startDate, draft.endDate) > maxPeriodDays) return `Слишком длинный период: не больше ${maxPeriodDays} дней`;
    if (today && leavePeriodDays(today, draft.startDate) - 1 > maxAheadDays) return 'Слишком далеко: заявки принимаются на год вперёд';
    if (!String(draft.reason || '').trim()) return 'Напишите причину';
    return '';
};

export const draftPayload = (draft) => ({
    kind: draft.kind,
    start_date: draft.startDate,
    end_date: draft.endDate,
    reason: String(draft.reason || '').trim(),
});

// ── Ссылка из Telegram: ?view=work_schedules&leave=<id> ─────────────────────

export const readLeaveLinkId = () => {
    try {
        const id = Number(new URLSearchParams(window.location.search).get('leave'));
        return Number.isFinite(id) && id > 0 ? id : null;
    } catch {
        return null;
    }
};

export const clearLeaveLink = () => {
    try {
        const url = new URL(window.location.href);
        if (!url.searchParams.has('leave')) return;
        url.searchParams.delete('leave');
        window.history.replaceState(window.history.state, '', url.toString());
    } catch {
        /* адрес не трогаем — это только чистка */
    }
};

/** Цель уведомления колокола о заявке: 'leave:<id>' → id, иначе null. */
export const leaveBellTargetId = (target) => {
    const match = /^leave:(\d+)$/.exec(String(target || ''));
    return match ? Number(match[1]) : null;
};

// ── API ─────────────────────────────────────────────────────────────────────

export const leaveApiError = (error, fallback = 'Не удалось выполнить действие') => {
    const data = error?.response?.data;
    if (data && typeof data === 'object' && data.error) return String(data.error);
    return fallback;
};

export const createLeaveApi = (apiBaseUrl, headers) => {
    const base = `${apiBaseUrl}/api/leave_requests`;
    const request = (method, path, { data, params } = {}) => axios({
        method,
        url: `${base}${path}`,
        data,
        params,
        headers: { 'Content-Type': 'application/json', ...headers() },
        withCredentials: true,
    }).then((response) => response.data);
    /* Файл приходит blob'ом — и отказ сервера тоже: его JSON лежит внутри blob,
       и без распаковки человек видел бы общий текст вместо настоящей причины. */
    const file = (path, params) => axios.get(`${base}${path}`, {
        params,
        headers: headers(),
        withCredentials: true,
        responseType: 'blob',
    }).then((response) => response.data).catch(async (error) => {
        const data = error?.response?.data;
        if (typeof Blob !== 'undefined' && data instanceof Blob && /json/i.test(data.type || '')) {
            try { error.response.data = JSON.parse(await data.text()); } catch { /* останется общий текст */ }
        }
        throw error;
    });
    return {
        mine: () => request('get', '/my'),
        create: (draft) => request('post', '/my', { data: draftPayload(draft) }),
        edit: (draft) => request('patch', `/my/${draft.id}`, { data: draftPayload(draft) }),
        cancel: (id) => request('post', `/my/${id}/cancel`, { data: {} }),
        answer: (id, accept, comment) => request('post', `/my/${id}/proposal`, { data: { accept, comment } }),
        seen: () => request('post', '/my/seen', { data: {} }),
        review: (status) => request('get', '/review', { params: { status } }),
        overlaps: (id, start, end) => request('get', `/review/${id}/overlaps`, {
            params: { start_date: start, end_date: end },
        }),
        decide: (id, body) => request('post', `/review/${id}`, { data: body }),
        journal: (query) => request('get', '/journal', { params: leaveJournalQuery(query) }),
        journalFile: (query) => file('/journal/export', leaveJournalQuery(query)),
        settings: () => request('get', '/settings'),
        saveSettings: (departmentId, body) => request('put', `/settings/${departmentId}`, { data: body }),
    };
};
