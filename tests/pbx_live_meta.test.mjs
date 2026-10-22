import test from 'node:test';
import assert from 'node:assert/strict';

import {
    callClock, callSubtitle, connectorStatus, elapsedSeconds, endedNote, mmss, pickLine, sortCalls,
    stateTone, wallSeconds,
} from '../src/components/cdr/liveMeta.js';

/* Подписи живых звонков (панель «Сейчас на линии»). Телефоны учебные. */

test('часы звонка: ожидание от начала, разговор от ответа, законченный — длина разговора', () => {
    const now = '2026-10-22T12:01:30';
    assert.equal(callClock({ state: 'queued', started_at: '2026-10-22T12:01:00' }, now), '0:30');
    assert.equal(callClock({ state: 'talking', started_at: '2026-10-22T12:00:00', answered_at: '2026-10-22T12:00:07' }, now), '1:23');
    assert.equal(callClock({ state: 'ended', answered_at: '2026-10-22T12:00:00', ended_at: '2026-10-22T13:02:05' }, now), '1:02:05');
    assert.equal(callClock({ state: 'ended', answered_at: '', ended_at: '2026-10-22T12:00:20' }, now), '', 'разговора не было — часов нет');
});

test('время станции сравнивается в своей шкале, пояс браузера не участвует', () => {
    assert.equal(elapsedSeconds('2026-10-22T23:59:50', '2026-10-23T00:00:05'), 15);
    assert.equal(wallSeconds('мусор'), null);
    assert.equal(elapsedSeconds('', '2026-10-22T12:00:00'), null);
});

test('mmss', () => {
    assert.equal(mmss(0), '0:00');
    assert.equal(mmss(65), '1:05');
    assert.equal(mmss(null), '');
    assert.equal(mmss(-3), '');
});

test('чем кончилось', () => {
    assert.equal(endedNote({ state: 'ended', answered_at: 'x', hangup_side: 'client' }), 'Разговор · положил клиент');
    assert.equal(endedNote({ state: 'ended', answered_at: 'x', hangup_side: '' }), 'Разговор');
    assert.equal(endedNote({ state: 'ended', answered_at: '', dial_status: 'ABANDON', direction: 'in' }), 'Клиент не дождался');
    assert.equal(endedNote({ state: 'ended', answered_at: '', dial_status: 'BUSY', direction: 'out' }), 'Занято');
    assert.equal(endedNote({ state: 'ended', answered_at: '', direction: 'out' }), 'Не ответил');
    assert.equal(endedNote({ state: 'ended', answered_at: '', direction: 'in' }), 'Не приняли');
    assert.equal(endedNote({ state: 'talking' }), '');
});

test('вторая строка: оператор, иначе кому звонит, иначе парк', () => {
    assert.equal(callSubtitle({ operator: 'Оператор Один', park: 'Jana такси' }), 'Оператор Один');
    assert.equal(callSubtitle({ ringing_operators: ['А', 'Б'], park: 'Jana такси' }), 'звонит: А, Б');
    assert.equal(callSubtitle({ park: 'Jana такси' }), 'Jana такси');
    assert.equal(callSubtitle({ queue: '3099' }), 'очередь 3099');
    assert.equal(callSubtitle({ direction: 'internal' }), 'Внутренний');
});

test('состояние коннектора: онлайн только на связи и со станцией', () => {
    assert.equal(connectorStatus(null).online, false);
    assert.equal(connectorStatus({ last_seen_at: 'x', connected: false }).label, 'коннектор не на связи');
    assert.equal(connectorStatus({ last_seen_at: 'x', connected: true, ami_connected: false }).label, 'станция недоступна');
    assert.deepEqual(connectorStatus({ last_seen_at: 'x', connected: true, ami_connected: true }),
        { tone: 'green', label: 'на связи', online: true });
    assert.equal(stateTone('talking'), 'green');
    assert.equal(stateTone('что-то'), 'slate');
});

test('порядок: идущие выше законченных, свежие первыми', () => {
    const sorted = sortCalls([
        { linkedid: 'a', state: 'ended', started_at: '2026-10-22T12:05:00' },
        { linkedid: 'b', state: 'queued', started_at: '2026-10-22T12:01:00' },
        { linkedid: 'c', state: 'talking', started_at: '2026-10-22T12:03:00' },
    ]);
    assert.deepEqual(sorted.map((c) => c.linkedid), ['c', 'b', 'a']);
});

test('линия для перезвона: по номеру линии, иначе последняя, иначе единственная', () => {
    const lines = [{ prefix: '3333', number: '7475550033' }, { prefix: '5050', number: '7005550050' }];
    assert.equal(pickLine(lines, '7005550050', '3333'), '5050');
    assert.equal(pickLine(lines, '7000000000', '3333'), '3333');
    assert.equal(pickLine(lines, '', '9999'), '');
    assert.equal(pickLine([lines[0]], '', ''), '3333');
});

test('изменения из потока: новее по ревизии, повтор кадра не откатывает', async () => {
    const { mergeLiveCalls } = await import('../src/components/cdr/liveMeta.js');
    const known = [{ linkedid: 'a', state: 'talking', rev: 5 }, { linkedid: 'b', state: 'queued', rev: 2 }];
    const merged = mergeLiveCalls(known, [
        { linkedid: 'a', state: 'ringing', rev: 4 },           // старый кадр после переподключения
        { linkedid: 'b', state: 'ended', rev: 6 },
        { linkedid: 'c', state: 'new', rev: 7 },
    ]);
    const byId = Object.fromEntries(merged.map((call) => [call.linkedid, call.state]));
    assert.deepEqual(byId, { a: 'talking', b: 'ended', c: 'new' });
});

test('ответ опроса: набор из ответа, но присланное потоком новее не откатывается', async () => {
    const { reconcileLiveCalls } = await import('../src/components/cdr/liveMeta.js');
    const known = [
        { linkedid: 'a', state: 'talking', rev: 9 },            // поток прислал позже, чем собран ответ
        { linkedid: 'b', state: 'queued', rev: 2 },             // в ответе его уже нет — ушёл из окна
    ];
    const result = reconcileLiveCalls(known, [
        { linkedid: 'a', state: 'ringing', rev: 8 },
        { linkedid: 'c', state: 'new', rev: 3 },
    ]);
    const byId = Object.fromEntries(result.map((call) => [call.linkedid, call.state]));
    assert.deepEqual(byId, { a: 'talking', c: 'new' });
    assert.deepEqual(reconcileLiveCalls(null, [{ linkedid: 'x', rev: 1 }]).map((c) => c.linkedid), ['x']);
});

test('законченный звонок уходит через 90 с после отбоя', async () => {
    const { visibleCalls } = await import('../src/components/cdr/liveMeta.js');
    const calls = [
        { linkedid: 'a', state: 'ended', ended_at: '2026-10-22T12:00:00' },
        { linkedid: 'b', state: 'ended', ended_at: '2026-10-22T12:01:00' },
        { linkedid: 'c', state: 'talking', started_at: '2026-10-22T11:00:00' },
    ];
    assert.deepEqual(visibleCalls(calls, '2026-10-22T12:01:31').map((c) => c.linkedid), ['b', 'c']);
    assert.equal(visibleCalls(calls, '').length, 3, 'без часов сервера — ничего не прячем');
});
