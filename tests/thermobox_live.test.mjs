// «Термокороба»: живой канал открытого экрана (src/components/thermoboxes/sheetPulse.js, сервер — thermoboxes/live.py).
// Сервер прочитал лист — экран сверяет /source сразу; пока тычки идут, опрос молчит; замолчал канал — опрос
// возвращается; скрытая вкладка держит поток ещё минуту, потом отпускает место.

import test from 'node:test';
import assert from 'node:assert/strict';

import { runSseStream } from '../src/components/common/sseClient.js';
import {
    FALLBACK_CHECK_MS, HIDDEN_GRACE_MS, SILENCE_MS, createSourceCheck, watchSheetSource,
} from '../src/components/thermoboxes/sheetPulse.js';

const fakeDocument = (state = 'visible') => {
    const listeners = new Set();
    return {
        visibilityState: state,
        addEventListener: (name, fn) => { if (name === 'visibilitychange') listeners.add(fn); },
        removeEventListener: (name, fn) => { if (name === 'visibilitychange') listeners.delete(fn); },
        set(next) {
            this.visibilityState = next;
            listeners.forEach((fn) => fn());
        },
        listeners,
    };
};

// Ручные часы: таймер срабатывает, только когда тест двигает время.
const manualClock = () => {
    let now = 0;
    let timers = [];
    return {
        setTimer: (fn, ms) => {
            const timer = { fn, at: now + ms };
            timers.push(timer);
            return timer;
        },
        clearTimer: (timer) => { timers = timers.filter((item) => item !== timer); },
        advance(ms) {
            const until = now + ms;
            for (;;) {
                const due = timers.filter((timer) => timer.at <= until).sort((a, b) => a.at - b.at)[0];
                if (!due) break;
                timers = timers.filter((timer) => timer !== due);
                now = due.at;
                due.fn();
            }
            now = until;
        },
        pending: () => timers.length,
    };
};

// Держатель соединения вместо runSseStream: запоминает каждое открытие, события и состояния подаёт тест.
const fakeStreams = () => {
    const opened = [];
    const runStream = (options) => {
        const stream = {
            options,
            closed: false,
            close() {
                this.closed = true;
                options.onState('closed');
            },
            state(next) { options.onState(next); },
            event(name) { options.onEvent(name, {}); },
        };
        opened.push(stream);
        return stream;
    };
    return { opened, runStream };
};

const watch = ({ doc = fakeDocument() } = {}) => {
    const streams = fakeStreams();
    const clock = manualClock();
    const pulses = [];
    const lives = [];
    const watcher = watchSheetSource({
        url: 'https://portal/api/thermoboxes/stream',
        getHeaders: () => ({ Authorization: 'Bearer t' }),
        runStream: streams.runStream,
        onPulse: () => pulses.push('pulse'),
        onLive: (value) => lives.push(value),
        doc,
        setTimer: clock.setTimer,
        clearTimer: clock.clearTimer,
    });
    return { doc, streams, clock, pulses, lives, watcher, last: () => streams.opened.at(-1) };
};

test('видимый экран открывает поток; событие source — сверка, прочие — нет', () => {
    const w = watch();
    assert.equal(w.streams.opened.length, 1);
    const { options } = w.last();
    assert.equal(options.url, 'https://portal/api/thermoboxes/stream');
    assert.deepEqual(options.getHeaders(), { Authorization: 'Bearer t' });
    w.last().event('source');
    w.last().event('chats');
    assert.equal(w.pulses.length, 1);
});

test('подключился — опрос не нужен и сверка сразу; переподключился — снова сверка', () => {
    const w = watch();
    w.last().state('connecting');
    assert.deepEqual(w.lives, []);
    w.last().state('live');
    assert.deepEqual(w.lives, [true]);
    assert.equal(w.pulses.length, 1, 'тычок, ушедший до подписки, иначе ждал бы следующего чтения листа');
    w.last().state('live');
    assert.equal(w.pulses.length, 1, 'повтор «live» — не новое подключение');
    w.last().state('connecting');
    assert.deepEqual(w.lives, [true, false], 'обрыв — опрос снова нужен');
    w.last().state('live');
    assert.equal(w.pulses.length, 2);
    assert.deepEqual(w.lives, [true, false, true]);
});

test('поток подключён, а тычков нет дольше SILENCE_MS — опрос возвращается и сверка сразу', () => {
    const w = watch();
    w.last().state('live');
    w.clock.advance(SILENCE_MS - 1000);
    w.last().event('source');                       // тычок — сторож перезапущен
    w.clock.advance(SILENCE_MS - 1000);
    assert.deepEqual(w.lives, [true], 'тычки идут — опрос не нужен');
    assert.equal(w.pulses.length, 2);
    w.clock.advance(2000);
    assert.deepEqual(w.lives, [true, false], 'слушатель на сервере лёг — экран на опрос');
    assert.equal(w.pulses.length, 3, 'и сверка сразу, не ждать опроса');
    w.last().event('source');
    assert.deepEqual(w.lives, [true, false, true], 'тычки вернулись — опрос снова не нужен');
});

test('мест нет или нет прав — канал не живой, экран остаётся на опросе', () => {
    for (const state of ['off', 'denied']) {
        const w = watch();
        w.last().state(state);
        assert.deepEqual(w.lives, [], state);
        assert.deepEqual(w.pulses, [], state);
    }
});

test('скрытая вкладка держит поток ещё минуту: вернулась раньше — тот же поток и сверка', () => {
    const w = watch();
    w.last().state('live');
    w.doc.set('hidden');
    w.clock.advance(HIDDEN_GRACE_MS - 1000);
    assert.equal(w.last().closed, false, 'частые переключения вкладок не плодят соединений');
    w.doc.set('visible');
    assert.equal(w.streams.opened.length, 1);
    assert.equal(w.pulses.length, 2, 'тычки в фоне экран пропускал — сверить при возврате');
    w.clock.advance(HIDDEN_GRACE_MS);
    assert.equal(w.last().closed, false, 'возврат отменил закрытие');
});

test('скрытая дольше минуты — поток закрыт и опрос; вернулась — новый поток и сверка после подключения', () => {
    const w = watch();
    w.last().state('live');
    w.doc.set('hidden');
    w.clock.advance(HIDDEN_GRACE_MS);
    assert.ok(w.streams.opened[0].closed, 'место под поток на сервере держит только экран, который смотрят');
    assert.deepEqual(w.lives, [true, false]);
    w.doc.set('visible');
    assert.equal(w.streams.opened.length, 2);
    assert.equal(w.pulses.length, 1, 'до подключения сверки нет — её сделает подключение');
    w.last().state('live');
    assert.equal(w.pulses.length, 2);
    w.clock.advance(FALLBACK_CHECK_MS);
    assert.equal(w.pulses.length, 2, 'подключился вовремя — запасной сверки нет');
});

test('вернулась, а поток не ожил за FALLBACK_CHECK_MS — сверка без него', () => {
    const w = watch();
    w.last().state('live');
    w.doc.set('hidden');
    w.clock.advance(HIDDEN_GRACE_MS);
    w.doc.set('visible');
    w.last().state('connecting');
    w.clock.advance(FALLBACK_CHECK_MS);
    assert.equal(w.pulses.length, 2);
});

test('вернулась на вкладку, где поток упёрся в 503, — сверка сразу, без нового потока', () => {
    const w = watch();
    w.last().state('off');
    w.doc.set('hidden');
    w.doc.set('visible');
    assert.equal(w.streams.opened.length, 1);
    assert.equal(w.pulses.length, 1);
});

test('скрытая с самого начала вкладка поток не открывает', () => {
    const w = watch({ doc: fakeDocument('hidden') });
    assert.equal(w.streams.opened.length, 0);
    w.doc.set('visible');
    assert.equal(w.streams.opened.length, 1);
});

test('stop — поток закрыт, таймеры сняты, вкладка его больше не открывает', () => {
    const w = watch();
    w.last().state('live');
    w.doc.set('hidden');
    w.watcher.stop();
    assert.ok(w.streams.opened[0].closed);
    assert.deepEqual(w.lives, [true, false]);
    assert.equal(w.doc.listeners.size, 0);
    assert.equal(w.clock.pending(), 0);
    w.doc.set('visible');
    assert.equal(w.streams.opened.length, 1);
    assert.equal(w.pulses.length, 1);
});

// С настоящим держателем соединения (sseClient.js) — как в браузере: «: connected» → живой, source → сверка.
const streamResponse = (text) => {
    const chunks = [new TextEncoder().encode(text)];
    return {
        status: 200,
        ok: true,
        headers: { get: () => null },
        body: {
            getReader: () => ({
                read: async () => (chunks.length ? { value: chunks.shift(), done: false } : new Promise(() => {})),
            }),
        },
    };
};

test('с настоящим sseClient: подключение — сверка, кадр source — ещё сверка, ping — ничего', async () => {
    const doc = fakeDocument();
    const pulses = [];
    const lives = [];
    const requests = [];
    const clock = manualClock();
    const watcher = watchSheetSource({
        url: 'https://portal/api/thermoboxes/stream',
        getHeaders: () => ({ Authorization: 'Bearer t' }),
        runStream: (options) => runSseStream({
            ...options,
            fetchImpl: async (url, init) => {
                requests.push([url, init.headers]);
                return streamResponse(': connected 1\n\n: ping 2\n\nevent: source\ndata: {}\n\n');
            },
            setTimer: clock.setTimer,
            clearTimer: clock.clearTimer,
        }),
        onPulse: () => pulses.push('pulse'),
        onLive: (value) => lives.push(value),
        doc,
        setTimer: clock.setTimer,
        clearTimer: clock.clearTimer,
    });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(requests.length, 1);
    assert.equal(requests[0][0], 'https://portal/api/thermoboxes/stream');
    assert.equal(requests[0][1].Accept, 'text/event-stream');
    assert.equal(requests[0][1].Authorization, 'Bearer t');
    assert.deepEqual(lives, [true]);
    assert.equal(pulses.length, 2, 'подключение — сверка, кадр source — ещё одна; ping — тишина');
    watcher.stop();
    assert.deepEqual(lives, [true, false]);
});

// ── Одна сверка за раз ────────────────────────────────────────────────

const deferred = () => {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
};

test('тычок посреди сверки — ещё одна сверка сразу после неё; сколько бы ни пришло — одна', async () => {
    const runs = [];
    let gate = deferred();
    const check = createSourceCheck({ run: () => { runs.push('run'); return gate.promise; } });
    const first = check();
    assert.equal(runs.length, 1);
    check();
    check();
    check();
    assert.equal(runs.length, 1, 'вторая сверка не идёт параллельно первой');
    const firstGate = gate;
    gate = deferred();
    firstGate.resolve();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(runs.length, 2, 'тычок посреди сверки не потерян: та могла уйти до записи на сервере');
    gate.resolve();
    await first;
    assert.equal(runs.length, 2);
    await check();
    assert.equal(runs.length, 3, 'после цикла сверка снова свободна');
});

test('скрытый экран не сверяется; скрылся посреди сверки — повтора нет', async () => {
    let visible = false;
    const runs = [];
    let gate = deferred();
    const check = createSourceCheck({ run: () => { runs.push('run'); return gate.promise; }, canRun: () => visible });
    await check();
    assert.equal(runs.length, 0);
    visible = true;
    const first = check();
    check();
    visible = false;
    gate.resolve();
    await first;
    assert.equal(runs.length, 1);
});

test('ошибка сверки не ломает следующую', async () => {
    let fail = true;
    const runs = [];
    const check = createSourceCheck({
        run: async () => {
            runs.push('run');
            if (fail) throw new Error('сеть');
        },
    });
    await check();
    fail = false;
    await check();
    assert.equal(runs.length, 2);
});
