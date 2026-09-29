import test from 'node:test';
import assert from 'node:assert/strict';

import {
    COMPLAINTS_FILTER, compareEntries, complaintEntry, complaintStatusFor, mergeFeeds, ticketEntry,
    withSortRank,
} from '../src/components/crm/feedMerge.js';

/* Одна лента «Обращений» из обращений и своих жалоб (решение владельца
 * 29.09.2026). Обе ленты порционные, и склейка обязана не переставлять строки
 * при догрузке: иначе строка, которую человек уже увидел, «прыгает» вниз. */

const ticket = (id, at, extra = {}) => ({ id, last_message_at: at, ...extra });
const complaint = (id, at, extra = {}) => ({ id, last_activity_at: at, ...extra });
const keys = (result) => result.items.map((entry) => entry.key);

test('свежее сверху, жалобы и обращения вперемешку', () => {
    const result = mergeFeeds({
        tickets: [ticket(10, '2026-09-29T12:00:00'), ticket(9, '2026-09-29T09:00:00')],
        complaints: [complaint(5, '2026-09-29T11:00:00')],
    });
    assert.deepEqual(keys(result), ['ticket:10', 'complaint:5', 'ticket:9']);
    assert.equal(result.loadMore, null);
});

test('в «Моих» непрочитанное наверху — как у обращений на сервере', () => {
    // Ленты приходят в серверном порядке: у обращений непрочитанное уже сверху.
    const result = mergeFeeds({
        tickets: [ticket(9, '2026-09-29T09:00:00', { unread: true }), ticket(10, '2026-09-29T12:00:00')],
        complaints: [complaint(5, '2026-09-29T08:00:00', { unread: true })],
        unreadFirst: true,
    });
    assert.deepEqual(keys(result), ['ticket:9', 'complaint:5', 'ticket:10']);
    // Без «Моих» (лента «Все», поиск) непрочитанное не поднимается.
    const plain = mergeFeeds({
        tickets: [ticket(10, '2026-09-29T12:00:00'), ticket(9, '2026-09-29T09:00:00', { unread: true })],
        complaints: [complaint(5, '2026-09-29T11:00:00', { unread: true })],
    });
    assert.deepEqual(keys(plain), ['ticket:10', 'complaint:5', 'ticket:9']);
});

test('прочитанная строка не уезжает из-под курсора до перечитывания', () => {
    // Как в ленте: ярус фиксируется на загрузке, открытие гасит только unread.
    const tickets = [ticket(9, '2026-09-29T09:00:00', { unread: true }), ticket(10, '2026-09-29T12:00:00')]
        .map(withSortRank);
    const complaints = [complaint(5, '2026-09-29T08:00:00', { unread: true })].map(withSortRank);
    const before = keys(mergeFeeds({ tickets, complaints, unreadFirst: true }));
    const read = [{ ...tickets[0], unread: false }, tickets[1]];
    const after = keys(mergeFeeds({ tickets: read, complaints, unreadFirst: true }));
    assert.deepEqual(after, before);
    assert.deepEqual(after, ['ticket:9', 'complaint:5', 'ticket:10']);
});

test('пока у обращений есть ещё порция, более старые жалобы ждут', () => {
    const result = mergeFeeds({
        tickets: [ticket(10, '2026-09-29T12:00:00'), ticket(9, '2026-09-29T10:00:00')],
        ticketsMore: true,
        complaints: [complaint(5, '2026-09-29T11:00:00'), complaint(4, '2026-09-28T08:00:00')],
    });
    // Жалоба №4 старше последнего загруженного обращения: следующая порция
    // обращений может встать перед ней — показывать её рано.
    assert.deepEqual(keys(result), ['ticket:10', 'complaint:5', 'ticket:9']);
    assert.equal(result.loadMore, 'tickets');
});

test('догружается та лента, чья граница выше', () => {
    const result = mergeFeeds({
        tickets: [ticket(10, '2026-09-29T12:00:00'), ticket(9, '2026-09-28T10:00:00')],
        ticketsMore: true,
        complaints: [complaint(5, '2026-09-29T11:00:00')],
        complaintsMore: true,
    });
    assert.deepEqual(keys(result), ['ticket:10', 'complaint:5']);
    assert.equal(result.loadMore, 'complaints');
});

test('догруженная порция не переставляет уже показанное', () => {
    const first = mergeFeeds({
        tickets: [ticket(10, '2026-09-29T12:00:00'), ticket(9, '2026-09-29T10:00:00')],
        ticketsMore: true,
        complaints: [complaint(5, '2026-09-29T11:00:00'), complaint(4, '2026-09-28T08:00:00')],
    });
    const second = mergeFeeds({
        tickets: [ticket(10, '2026-09-29T12:00:00'), ticket(9, '2026-09-29T10:00:00'),
            ticket(8, '2026-09-29T09:00:00')],
        complaints: [complaint(5, '2026-09-29T11:00:00'), complaint(4, '2026-09-28T08:00:00')],
    });
    assert.deepEqual(keys(second).slice(0, first.items.length), keys(first));
    assert.deepEqual(keys(second), ['ticket:10', 'complaint:5', 'ticket:9', 'ticket:8', 'complaint:4']);
});

test('номера у жалоб и обращений свои — ключи не пересекаются', () => {
    assert.notEqual(ticketEntry(ticket(7, 'x')).key, complaintEntry(complaint(7, 'x')).key);
    // Одно и то же время: порядок всё равно устойчивый.
    const a = ticketEntry(ticket(7, '2026-09-29T10:00:00'));
    const b = complaintEntry(complaint(7, '2026-09-29T10:00:00'));
    assert.ok(compareEntries(a, b) < 0);
});

test('фильтры состояния — в статусы жалоб', () => {
    assert.equal(complaintStatusFor('active'), 'open');
    assert.equal(complaintStatusFor('answered'), 'answered');
    // «Закрытые» у автора — любые закрытые, и зафиксированные тоже.
    assert.equal(complaintStatusFor('closed'), 'done');
    assert.equal(complaintStatusFor('all'), '');
    assert.equal(COMPLAINTS_FILTER, 'complaints');
});
