import test from 'node:test';
import assert from 'node:assert/strict';

import { createSseParser, runSseStream } from '../src/components/common/sseClient.js';

/* Клиент SSE на fetch (src/components/common/sseClient.js): разбор кадров и переподключение. */

test('кадры разбираются, даже если сеть режет их где угодно', () => {
    const parser = createSseParser();
    const text = ': connected 1\n\nevent: live\ndata: {"a":1}\n\n: ping 2\n\nevent: bye\ndata: {}\n\n';
    const out = [];
    for (let i = 0; i < text.length; i += 5) out.push(...parser.feed(text.slice(i, i + 5)));
    assert.deepEqual(out, [
        { event: '', data: 'connected 1' },
        { event: 'live', data: '{"a":1}' },
        { event: '', data: 'ping 2' },
        { event: 'bye', data: '{}' },
    ]);
});

const streamResponse = (text) => {
    const chunks = [new TextEncoder().encode(text)];
    return {
        status: 200,
        ok: true,
        headers: { get: () => null },
        body: {
            getReader: () => ({
                read: async () => (chunks.length ? { value: chunks.shift(), done: false } : { value: undefined, done: true }),
            }),
        },
    };
};

const manualTimers = () => {
    const timers = [];
    return {
        timers,
        setTimer: (fn, ms) => { const t = { fn, ms }; timers.push(t); return t; },
        clearTimer: (t) => { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); },
    };
};

test('кадры доходят до onEvent, bye — переподключение сразу, обрыв — с паузой', async () => {
    const calls = [];
    const events = [];
    const states = [];
    const clock = manualTimers();
    const responses = [
        streamResponse(': connected 1\n\nevent: live\ndata: {"now":{"queue":1}}\n\nevent: bye\ndata: {}\n\n'),
        streamResponse(': connected 2\n\n'),          // закрылся без прощания
    ];
    const stream = runSseStream({
        url: 'https://portal/api/op_wallboard/live',
        getHeaders: () => ({ Authorization: 'Bearer t' }),
        onEvent: (event, payload) => events.push([event, payload]),
        onState: (state) => states.push(state),
        fetchImpl: async (url, options) => { calls.push([url, options.headers]); return responses.shift(); },
        random: () => 0,
        setTimer: clock.setTimer,
        clearTimer: clock.clearTimer,
    });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(events, [['live', { now: { queue: 1 } }]]);
    assert.equal(calls[0][1].Accept, 'text/event-stream');
    assert.equal(calls[0][1].Authorization, 'Bearer t');
    assert.ok(states.includes('live'));
    const replan = clock.timers.find((t) => t.ms === 50);
    assert.ok(replan, 'плановое переподключение после bye — без паузы');
    replan.fn();
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(calls.length, 2);
    const backoff = clock.timers.find((t) => t.ms === 2000);
    assert.ok(backoff, 'закрытие без прощания — пауза перед повтором');
    stream.close();
    assert.equal(states.at(-1), 'closed');
});

test('503 — живём на опросе и ждём Retry-After, 403 — пять минут', async () => {
    const clock = manualTimers();
    const states = [];
    const replies = [
        { status: 503, ok: false, headers: { get: (name) => (name === 'Retry-After' ? '120' : null) } },
        { status: 403, ok: false, headers: { get: () => null } },
    ];
    const stream = runSseStream({
        url: 'u', onEvent: () => {}, onState: (s) => states.push(s),
        fetchImpl: async () => replies.shift(), random: () => 0,
        setTimer: clock.setTimer, clearTimer: clock.clearTimer,
    });
    await new Promise((resolve) => setImmediate(resolve));
    assert.ok(states.includes('off'));
    const retry = clock.timers.find((t) => t.ms === 120000);
    assert.ok(retry, 'Retry-After соблюдён');
    retry.fn();
    await new Promise((resolve) => setImmediate(resolve));
    assert.ok(states.includes('denied'));
    assert.ok(clock.timers.find((t) => t.ms === 300000));
    stream.close();
});
