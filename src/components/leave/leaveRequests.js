import axios from 'axios';

/*
 * Заявки на отпуск и Б/С (задача #358) — общие правила экрана, без React.
 *
 * Оператор подаёт заявку тем же «Новым запросом», что и заявки по сменам
 * (вид «Отпуск или Б/С»), СВ и глава разбирают её во вкладке очереди «Запросы».
 * Правила маршрута живут на сервере (leave_requests/rules.py) — здесь только
 * подписи, счёт дней и проверка формы ради подсказки, а не вместо права.
 */

// Значение вида в общем списке «Что нужно» окна заявки. Это не код заявки, а
// пункт списка: конкретный вид (отпуск или Б/С) выбирается уже в форме.
export const LEAVE_REQUEST_OPTION = 'leave';

export const LEAVE_KINDS = ['annual_leave', 'bs'];

// Коды — те же, что у статусов графика: утверждённая заявка ставит ровно такой период.
export const LEAVE_KIND_LABELS = {
    annual_leave: 'Ежегодный отпуск',
    bs: 'Б/С',
};

// Подписи сегментов формы: два варианта — сегменты, оба названы словами.
export const LEAVE_KIND_SEGMENTS = {
    annual_leave: 'Отпуск',
    bs: 'Б/С',
};

export const LEAVE_KIND_HINTS = [
    'Отпуск — ежегодный оплачиваемый отпуск.',
    'Б/С — отпуск без сохранения заработной платы.',
];

export const leaveOptionLabel = (kinds = []) => {
    if (kinds.length === 1) return kinds[0] === 'bs' ? 'Взять Б/С' : 'Взять отпуск';
    return 'Отпуск или Б/С';
};

export const leaveModalTitle = (draft, kinds = []) => {
    if (draft?.id) return 'Изменить заявку';
    if (kinds.length === 1) return kinds[0] === 'bs' ? 'Заявка на Б/С' : 'Заявка на отпуск';
    return 'Отпуск или Б/С';
};

// Строка «i» у списка «Что нужно» окна заявки.
export const LEAVE_OPTION_HINT = 'Отпуск или Б/С — на период: сначала согласует супервайзер, затем утверждает руководитель отдела, и период сам встаёт в график.';

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

/** «Ежегодный отпуск · 01.12 — 14.12 · 14 дней». */
export const leaveSummary = (item, today = '') => [
    LEAVE_KIND_LABELS[item?.kind] || 'Заявка',
    formatLeavePeriod(item?.startDate, item?.endDate, today),
    daysText(leavePeriodDays(item?.startDate, item?.endDate)),
].filter(Boolean).join(' · ');

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
export const leaveDraftProblem = (draft, { today = '', kinds = [], maxPeriodDays = 365, maxAheadDays = 365 } = {}) => {
    if (!draft) return 'Заполните заявку';
    if (!draft.kind || (kinds.length && !kinds.includes(draft.kind) && !draft.id)) return 'Выберите: отпуск или Б/С';
    if (!draft.startDate) return 'Выберите первый день';
    if (!draft.endDate) return 'Выберите последний день';
    if (today && draft.startDate < today) return 'Начало уже прошло — выберите сегодняшний день или позже';
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
        settings: () => request('get', '/settings'),
        saveSettings: (departmentId, body) => request('put', `/settings/${departmentId}`, { data: body }),
    };
};
