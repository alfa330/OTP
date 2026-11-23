/*
 * Отчёт за день «Воронки найма» (задача HR-3): чистая логика экрана — без React и без запросов, чтобы её проверял
 * node --test (tests/hiring_day_report.test.mjs). Цифры считает сервер (hiring/day_report.py), здесь — какой вид
 * отчёта открыть, как подписать длительность, что уходит в запрос и какую цифру можно раскрыть списком.
 */

/* Вид отчёта запоминается у человека: HR открывает «За день» каждый вечер, руководитель — дашборд. */
export const REPORT_VIEW_KEY = 'hiring.reports.view';

/** Виды вкладки «Отчёты» по правам: отчёт за день — тем, кто работает с откликами (рекрутер, руководитель рекрутинга);
 *  у главы отдела без выдачи рекрутера его нет — там нечего считать. */
export const reportViews = (caps = {}) => [
    (caps.recruiter || caps.lead) && { value: 'day', label: 'За день' },
    { value: 'dashboard', label: 'Дашборд' },
    { value: 'motivation', label: 'Мотивация' },
].filter(Boolean);

/** Вид при открытии: запомненный, если он ещё доступен, иначе первый — «За день». */
export const pickReportView = (stored, views) => {
    const list = Array.isArray(views) ? views : [];
    return list.some((item) => item.value === stored) ? stored : (list[0]?.value || 'dashboard');
};

/** «1:29», «1:02:05» — длительность разговора; null — разговоров с длительностью не было. */
export const talkClock = (seconds) => {
    if (seconds === null || seconds === undefined || seconds === '' || !Number.isFinite(Number(seconds))) return null;
    const total = Math.max(0, Math.round(Number(seconds)));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = String(total % 60).padStart(2, '0');
    return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${secs}` : `${minutes}:${secs}`;
};

/** Запрос отчёта: день и рекрутер; без рекрутера — вся команда (рекрутеру сервер всё равно отдаёт только его отчёт). */
export const dayReportQuery = (day, recruiterId) => {
    const params = { day: day || undefined, recruiter_id: recruiterId ? String(recruiterId) : undefined };
    return Object.fromEntries(Object.entries(params).filter(([, value]) => value !== undefined));
};

/** Ключ цифры для списка карточек — тот же, что у сервера (day_report.build, ids). */
export const cellKey = (rowKey, column) => `${rowKey}:${column}`;
export const personKey = (userId, field) => `rec:${userId}:${field}`;
/** Строка «Итого» таблиц по рекрутерам раскрывается теми же карточками, что столбец «Итого» воронки. */
export const totalKey = (field) => cellKey(field, 'all');

/** Раскрывается ли цифра списком. Ноль — нечего показывать. «Поступило» у рекрутера без прав руководителя — нет: пул
 *  общий, а чужие взятые карточки ему не видны, и список вышел бы короче цифры. */
export const cellOpens = (rowKey, value, caps = {}) => Number(value) > 0 && (Boolean(caps.reports) || rowKey !== 'arrived');

/** Строки «из них» — подряд после «Отработано»: подпись «из них» стоит перед первой. */
export const firstPartIndex = (rows) => (Array.isArray(rows) ? rows.findIndex((row) => row.part) : -1);

/** Чей отчёт и на какой момент — подпись рядом с заголовком (отчёт за сегодня живой: «на 18:40»). */
export const scopeLabel = (data) => {
    const who = data?.recruiter ? (data.recruiter.name || 'Рекрутер') : 'Все рекрутеры';
    return data?.as_of ? `${who} · на ${data.as_of}` : who;
};

/** «Другая работа»: своя запись (в поле) и чужие (только чтение, непустые). */
export const splitNotes = (notes) => {
    const list = Array.isArray(notes) ? notes : [];
    return {
        mine: list.find((note) => note.mine) || null,
        others: list.filter((note) => !note.mine && String(note.body || '').trim()),
    };
};

/** Своя запись «Другой работы» после сохранения — в данных отчёта, чтобы лист не ждал перезагрузки; пустая — убрана. */
export const withMyNote = (notes, body, me) => {
    const list = (Array.isArray(notes) ? notes : []).filter((note) => !note.mine);
    if (!String(body || '').trim()) return list;
    return [...list, { user_id: me?.id ?? null, name: me?.name || '', body, mine: true }];
};

/** Статус карточки в списке за цифрой: у «В работе» и «Отказа» — подстатус («Недозвон / не ответил», «Наш отказ»),
 *  иначе список «Недозвон» показывал бы у всех «В работе». */
export const cardStatus = (card, stageLabels = {}, substageLabels = {}) => {
    const stage = card?.stage;
    const sub = card?.substage;
    if ((stage === 'in_work' || stage === 'refused') && sub && substageLabels[sub]) return substageLabels[sub];
    return stageLabels[stage] || stage || '';
};

export const NOTE_MAX = 2000;
/* Счётчик знаков появляется только у края предела — раньше он шум. */
export const NOTE_COUNTER_FROM = 1800;
export const noteCounter = (text) => {
    const length = String(text || '').length;
    return length >= NOTE_COUNTER_FROM ? `${length} / ${NOTE_MAX}` : '';
};
