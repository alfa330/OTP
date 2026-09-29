/* Одна лента «Обращений» из двух источников: обращения и свои жалобы.
 *
 * Жалобу оператор заводит в «Обращениях» и там же видит ответ для водителя и
 * вопрос группы (решение владельца 29.09.2026). Хранятся жалобы отдельно — у
 * них свой доступ (сотрудник, на которого жалуются, её не видит) и свой цикл в
 * группе, — поэтому сервер отдаёт их отдельной лентой, а склеиваются они здесь.
 *
 * Обе ленты порционные. Склейка обязана соблюдать три вещи:
 *   порядок  — тот же, что у обращений на сервере (crm/queries.list_tickets):
 *              в «Моих» без поиска непрочитанное наверху, дальше свежее;
 *   покой    — прочитанная строка не уезжает из-под курсора, пока список не
 *              перечитан (см. ticketList.markTicketSeen). Поэтому ярус
 *              «непрочитано» берётся на момент загрузки (sort_unread), а
 *              порядок внутри каждой ленты — ровно серверный: две ленты
 *              сливаются двумя указателями, без пересортировки;
 *   границу  — пока у одной ленты есть ещё порция, строки другой, которые
 *              идут ПОСЛЕ её последней загруженной строки, держим: следующая
 *              порция может встать перед ними, и строка «прыгнула» бы вниз.
 * «Показать ещё» догружает ту ленту, чья граница сейчас ограничивает показ. */

// Значение фильтра «Группа», при котором в ленте только жалобы.
export const COMPLAINTS_FILTER = 'complaints';

/* Фильтр состояния ленты → статус жалоб (complaints/queries.list_complaints).
 * «Закрытые» у автора — любые закрытые: и отработанные, и зафиксированные. */
const COMPLAINT_STATUS_BY_STATE = { active: 'open', answered: 'answered', closed: 'done', all: '' };

export const complaintStatusFor = (stateKey) => COMPLAINT_STATUS_BY_STATE[stateKey] ?? '';

/* Ярус «непрочитано» на момент загрузки — ставится строкам сразу из ответа
 * сервера. Открытие карточки гасит unread, но не sort_unread: строка стоит,
 * где стояла, до следующей загрузки списка. */
export const withSortRank = (item) => ({ ...item, sort_unread: Boolean(item && item.unread) });

const KIND_ORDER = { ticket: 0, complaint: 1 };

export const ticketEntry = (ticket) => ({
    kind: 'ticket',
    key: `ticket:${ticket.id}`,
    item: ticket,
    at: ticket.last_message_at || ticket.created_at || '',
});

export const complaintEntry = (complaint) => ({
    kind: 'complaint',
    key: `complaint:${complaint.id}`,
    item: complaint,
    at: complaint.last_activity_at || complaint.created_at || '',
});

const sortUnread = (entry) => (entry.item.sort_unread ?? entry.item.unread);

/* Порядок строк: <0 — a выше b. Время — наивное ISO Алматы у обеих лент,
 * поэтому сравнивается как есть. */
export const compareEntries = (a, b, unreadFirst = false) => {
    if (unreadFirst) {
        const rank = (entry) => (sortUnread(entry) ? 0 : 1);
        if (rank(a) !== rank(b)) return rank(a) - rank(b);
    }
    const at = (entry) => Date.parse(entry.at) || 0;
    if (at(a) !== at(b)) return at(b) - at(a);
    if (a.kind !== b.kind) return KIND_ORDER[a.kind] - KIND_ORDER[b.kind];
    return Number(b.item.id) - Number(a.item.id);
};

/* Склейка. Возвращает { items, loadMore }: items — строки в порядке показа,
 * loadMore — какую ленту догружать ('tickets' | 'complaints') или null, если
 * загружено всё. */
export const mergeFeeds = ({
    tickets = [], ticketsMore = false, complaints = [], complaintsMore = false,
    unreadFirst = false,
}) => {
    const a = tickets.map(ticketEntry);
    const b = complaints.map(complaintEntry);
    const merged = [];
    let i = 0;
    let j = 0;
    while (i < a.length || j < b.length) {
        if (j >= b.length || (i < a.length && compareEntries(a[i], b[j], unreadFirst) <= 0)) {
            merged.push(a[i]);
            i += 1;
        } else {
            merged.push(b[j]);
            j += 1;
        }
    }

    const bounds = [];
    if (ticketsMore && a.length) bounds.push({ source: 'tickets', entry: a[a.length - 1] });
    if (complaintsMore && b.length) bounds.push({ source: 'complaints', entry: b[b.length - 1] });
    if (!bounds.length) return { items: merged, loadMore: null };

    // Ограничивает показ та граница, что выше: за ней ещё не всё известно.
    // Всё, что склейка поставила после неё, — строки другой ленты, их держим.
    const limit = bounds.reduce((best, bound) => (
        merged.indexOf(bound.entry) < merged.indexOf(best.entry) ? bound : best));
    return {
        items: merged.slice(0, merged.indexOf(limit.entry) + 1),
        loadMore: limit.source,
    };
};
