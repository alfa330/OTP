import test from 'node:test';
import assert from 'node:assert/strict';

import {
    chatInitials, dayLabel, insertAt, listTime, localStamp, matchTemplates, mergeMessages, mergeThread, newClientId, pendingStatus,
    pendingTail, showAuthor, slashQuery, tickStatus, withDays,
} from '../src/components/hiring/hiringChats.js';

const TODAY = '2026-11-19';

test('list time: today — hours, yesterday — «вчера», earlier — day.month (Almaty strings, no timezone shift)', () => {
    assert.equal(listTime('2026-11-19T09:05:00', TODAY), '09:05');
    assert.equal(listTime('2026-11-18T23:59:00', TODAY), 'вчера');
    assert.equal(listTime('2026-11-06T17:27:00', TODAY), '06.11');
    assert.equal(listTime('2026-03-01T00:00:00', '2026-03-02'), 'вчера');   // через границу месяца
    assert.equal(listTime(null, TODAY), '');
});

test('day pills: Сегодня, Вчера, «6 ноября», other year with the year', () => {
    assert.equal(dayLabel('2026-11-19', TODAY), 'Сегодня');
    assert.equal(dayLabel('2026-11-18', TODAY), 'Вчера');
    assert.equal(dayLabel('2026-11-06', TODAY), '6 ноября');
    assert.equal(dayLabel('2025-12-31', TODAY), '31 декабря 2025');
    const rows = withDays([{ id: 1, at: '2026-11-18T10:00:00' }, { id: 2, at: '2026-11-18T11:00:00' },
        { id: 3, at: '2026-11-19T08:00:00' }], TODAY);
    assert.deepEqual(rows.map((row) => row.label || row.id), ['Вчера', 1, 2, 'Сегодня', 3]);
});

test('thread merge: the fresh last page replaces its copies in earlier pages', () => {
    const older = [{ id: 1 }, { id: 2 }, { id: 3, status: 'sent' }];
    const latest = [{ id: 3, status: 'read' }, { id: 4 }];
    assert.deepEqual(mergeThread(older, latest), [{ id: 1 }, { id: 2 }, { id: 3, status: 'read' }, { id: 4 }]);
    assert.deepEqual(mergeThread([], null), []);
});

test('pending tail: server outbox first, typed-now items disappear once the server has the same client id', () => {
    const tail = pendingTail([{ outbox_id: 7, client_id: 'c1', status: 'handed' }],
        [{ client_id: 'c1', status: 'queued' }, { client_id: 'c2', status: 'queued' }]);
    assert.deepEqual(tail.map((item) => item.key), ['o:7', 'l:c2']);
    assert.equal(pendingStatus({ status: 'failed' }), 'failed');
    assert.equal(pendingStatus({ status: 'handed' }), 'queued');
});

test('author over our message: first in a run of one author, again after a day pill or another author', () => {
    const a = { from_me: true, author: 'Айсулу', at: '2026-11-19T10:00:00' };
    const b = { from_me: true, author: 'Айсулу', at: '2026-11-19T10:01:00' };
    const c = { from_me: true, author: 'Надира', at: '2026-11-19T10:02:00' };
    const phone = { from_me: true, author: null, at: '2026-11-19T10:03:00' };
    assert.equal(showAuthor(a, null), true);
    assert.equal(showAuthor(b, a), false);
    assert.equal(showAuthor(c, b), true);
    assert.equal(showAuthor(phone, c), false);           // ответ с телефона — без автора
    assert.equal(showAuthor({ from_me: false, author: 'x' }, null), false);
});

test('templates by «/»: only at the very start of the field, search in title and text', () => {
    assert.equal(slashQuery('/при'), 'при');
    assert.equal(slashQuery('/'), '');
    assert.equal(slashQuery('текст /при'), null);
    assert.equal(slashQuery('/при\nвторая строка'), null);
    const items = [{ title: 'Приглашение', body: 'Ждём в офисе' }, { title: 'Адрес', body: 'Нурлы Тау 17к4б' }];
    assert.deepEqual(matchTemplates(items, 'нурлы').map((item) => item.title), ['Адрес']);
    assert.deepEqual(matchTemplates(items, '').map((item) => item.title), ['Приглашение', 'Адрес']);
});

test('insert at caret and client ids', () => {
    assert.deepEqual(insertAt('Добрый день', 7, 7, '😊 '), { value: 'Добрый 😊 день', caret: 10 });
    assert.deepEqual(insertAt('abc', undefined, undefined, '!'), { value: 'abc!', caret: 4 });
    const id = newClientId(() => 0.5);
    assert.match(id, /^[A-Za-z0-9_-]{8,64}$/);           // формат, который принимает сервер (wa_chat.CLIENT_ID_RE)
    assert.notEqual(newClientId(), newClientId());
});

test('thread keeps everything seen: older pages and a fresh page that moved past the junction', () => {
    // Открыли 41–100, догрузили 1–40, пришло три новых — свежая страница теперь 44–103: 41–43 не теряются.
    const range = (from, to, extra = {}) => Array.from({ length: to - from + 1 }, (_, i) => ({
        id: from + i, at: `2026-11-19T10:${String(from + i).padStart(2, '0')}:00`, ...extra }));
    let all = mergeMessages([], range(41, 100));
    all = mergeMessages(all, range(1, 40));
    all = mergeMessages(all, range(44, 103, { status: 'read' }));
    assert.equal(all.length, 103);
    assert.deepEqual(all.slice(0, 3).map((m) => m.id), [1, 2, 3]);
    assert.equal(all.find((m) => m.id === 50).status, 'read');      // свежая копия заменяет старую
    assert.equal(all.find((m) => m.id === 42).status, undefined);
});

test('pending tail: typed item stays hidden once the server has seen its client id, even after it was sent', () => {
    const seen = new Set(['c1']);
    assert.deepEqual(pendingTail([], [{ client_id: 'c1', status: 'failed' }, { client_id: 'c2' }], seen).map((i) => i.key), ['l:c2']);
});

test('ticks: «played» is shown as read; own fresh message time is local Almaty clock, server format', () => {
    assert.equal(tickStatus('played'), 'read');
    assert.equal(tickStatus(null), 'sent');
    assert.equal(localStamp(new Date(2026, 10, 19, 7, 5, 9)), '2026-11-19T07:05:09');
    // Вчерашнее «Не отправлено» после сегодняшних сообщений — плашки дня с разными ключами.
    const rows = withDays([{ id: 1, at: '2026-11-18T22:30:00' }, { id: 2, at: '2026-11-19T09:00:00' },
        { key: 'o:5', at: '2026-11-18T22:31:00' }], '2026-11-19');
    const keys = rows.filter((r) => r.dayKey).map((r) => r.dayKey);
    assert.equal(new Set(keys).size, keys.length);
});

test('avatar letters: real letters only — an emoji name is not cut in half', () => {
    assert.equal(chatInitials('Асель Нурланова'), 'АН');
    assert.equal(chatInitials('💌'), '');
    assert.equal(chatInitials('❤️ Айгерим'), 'А');
    assert.equal(chatInitials('~Meruert~'), 'M');
    assert.equal(chatInitials('Кандидат №12'), 'К1');
    assert.equal(chatInitials(''), '');
});
