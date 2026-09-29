/* Одна лента «Обращений» из двух источников: обращения и свои жалобы.
 *
 * Жалобу оператор заводит в «Обращениях» и там же видит ответ для водителя и
 * вопрос группы (решение владельца 29.09.2026). Хранятся жалобы отдельно — у
 * них свой доступ (сотрудник, на которого жалуются, её не видит) и свой цикл в
 * группе, — поэтому сервер отдаёт их отдельной лентой, а склеиваются они здесь.
 *
 * Обе ленты порционные. Склейка обязана соблюдать две вещи:
 *   порядок  — тот же, что у обращений на сервере (crm/queries.list_tickets):
 *              в «Моих» без поиска непрочитанное наверху, дальше свежее;
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

/* Порядок строк: <0 — a выше b. Время — наивное ISO Алматы у обеих лент,
 * поэтому сравнивается как есть. */
export const compareEntries = (a, b, unreadFirst = false) => {
    if (unreadFirst) {
        const rank = (entry) => (entry.item.unread ? 0 : 1);
        if (rank(a) !== rank(b)) return rank(a) - rank(b);
    }
    const at = (entry) => Date.parse(entry.at) || 0;
    if (at(a) !== at(b)) return at(b) - at(a);
    if (a.kind !== b.kind) return KIND_ORDER[a.kind] - KIND_ORDER[b.kind];
    return Number(b.item.id) - Number(a.item.id);
};

const lastOf = (entries, unreadFirst) => entries.reduce(
    (last, entry) => (!last || compareEntries(entry, last, unreadFirst) > 0 ? entry : last), null);

/* Склейка. Возвращает { items, loadMore }: items — строки в порядке показа,
 * loadMore — какую ленту догружать ('tickets' | 'complaints') или null, если
 * загружено всё. */
export const mergeFeeds = ({
    tickets = [], ticketsMore = false, complaints = [], complaintsMore = false,
    unreadFirst = false,
}) => {
    const ticketEntries = tickets.map(ticketEntry);
    const complaintEntries = complaints.map(complaintEntry);
    const all = ticketEntries.concat(complaintEntries)
        .sort((a, b) => compareEntries(a, b, unreadFirst));

    const bounds = [];
    if (ticketsMore && ticketEntries.length) {
        bounds.push({ source: 'tickets', entry: lastOf(ticketEntries, unreadFirst) });
    }
    if (complaintsMore && complaintEntries.length) {
        bounds.push({ source: 'complaints', entry: lastOf(complaintEntries, unreadFirst) });
    }
    if (!bounds.length) return { items: all, loadMore: null };

    // Ограничивает показ та граница, что выше: за ней ещё не всё известно.
    const limit = bounds.reduce((best, bound) => (
        compareEntries(bound.entry, best.entry, unreadFirst) < 0 ? bound : best));
    return {
        items: all.filter((entry) => compareEntries(entry, limit.entry, unreadFirst) <= 0),
        loadMore: limit.source,
    };
};
