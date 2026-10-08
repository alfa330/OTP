import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { splitPilotEvents, pilotChatKey } from '../src/components/wazzup/chatPilot.js';

// Execute the production hook with deterministic React scheduling, HTTP streams
// and a virtual clock. No external connections or browser credentials.
const source = readFileSync(new URL('../src/components/wazzup/useChatPilot.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace('export default function', 'function');
const makeHook = new Function('useEffect', 'useRef', 'useState', 'axios', 'splitPilotEvents',
    'pilotChatKey', 'document', 'fetch', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date',
    source + '\nreturn useChatPilot;');

function harness(overrides = {}) {
    const slots = [], timers = new Map(), streams = [];
    let now = 0, timerId = 0, index = 0, dirty = true, mounted = true, effects = [], output;
    const calls = { capability: 0, thread: 0, list: 0 };
    const options = { apiBaseUrl: '/fixture', user: { login: 'alfa330' }, account: 'op', active: true,
        headers: () => ({ Authorization: 'fixture' }), selected: { channelId: 'c', chatId: 'one' },
        refreshThread: async () => { calls.thread++; }, refreshList: async () => { calls.list++; }, ...overrides };
    let visibility;
    const document = { hidden: false, addEventListener: (_, callback) => { visibility = callback; },
        removeEventListener: () => { visibility = null; } };
    const schedule = (fn, delay, interval = false) => {
        const id = ++timerId; timers.set(id, { fn, at: now + delay, interval, delay }); return id;
    };
    const clear = (id) => timers.delete(id);
    const fetch = async (_, { signal }) => {
        let pending;
        const frames = [];
        const stream = { signal, emit(frame) {
            const chunk = { done: false, value: new TextEncoder().encode(frame) };
            if (pending) { const resolve = pending.resolve; pending = null; resolve(chunk); }
            else frames.push(chunk);
        } };
        signal.addEventListener('abort', () => { pending?.reject(new Error('aborted')); pending = null; });
        streams.push(stream);
        return { ok: true, body: { getReader: () => ({
            read: () => frames.length ? Promise.resolve(frames.shift())
                : signal.aborted ? Promise.reject(new Error('aborted'))
                    : new Promise((resolve, reject) => { pending = { resolve, reject }; }),
            cancel: async () => { pending?.resolve({ done: true }); pending = null; }, releaseLock() {},
        }) } };
    };
    const hook = makeHook((fn, deps) => {
        const i = index++, old = slots[i];
        if (old && deps.every((dep, k) => Object.is(dep, old.deps[k]))) return;
        slots[i] = { deps };
        effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); });
    }, (initial) => {
        const i = index++; return slots[i] ||= { current: initial };
    }, (initial) => {
        const i = index++;
        slots[i] ||= { value: typeof initial === 'function' ? initial() : initial };
        return [slots[i].value, (next) => {
            const value = typeof next === 'function' ? next(slots[i].value) : next;
            if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; }
        }];
    }, { get: async () => { calls.capability++; return { data: { enabled: true, canSend: true } }; } },
    splitPilotEvents, pilotChatKey, document, fetch, schedule, clear,
    (fn, delay) => schedule(fn, delay, true), clear, { now: () => now });

    const flush = async () => {
        for (let step = 0; step < 25; step++) {
            if (mounted && dirty) {
                index = 0; dirty = false; output = hook(options);
                const pending = effects; effects = []; pending.forEach((fn) => fn());
            }
            await Promise.resolve();
        }
    };
    return { calls, streams, timers, options, flush,
        get output() { return output; },
        async emit(event, data = {}) {
            streams.at(-1).emit(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
            await flush();
        },
        async tick(ms) {
            const end = now + ms;
            for (;;) {
                const entry = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
                if (!entry) break;
                const [id, timer] = entry; now = timer.at; timers.delete(id);
                if (timer.interval) timers.set(id, { ...timer, at: now + timer.delay });
                timer.fn(); await flush();
            }
            now = end; await flush();
        },
        async visible(value) { document.hidden = !value; visibility?.(); await flush(); },
        async close() { mounted = false; slots.forEach((slot) => slot?.cleanup?.()); await flush(); },
    };
}

test('pilot opens no realtime or capability request for other users/accounts', async () => {
    for (const options of [{ user: { login: 'operator' } }, { account: 'potok' }]) {
        const h = harness(options); await h.flush();
        assert.equal(h.calls.capability, 0); assert.equal(h.streams.length, 0);
        await h.close(); assert.equal(h.timers.size, 0);
    }
});

test('hydrated/status changes apply directly without any pane HTTP refresh; metadata fallback still repairs', async () => {
    const applied = [];
    const h = harness({ onChanges: (changes) => {
        applied.push(...changes);
        return { list: changes.some((e) => e.affectsList && !e.chat),
            thread: changes.some((e) => !e.statusOnly && !e.message) };
    } });
    await h.flush();
    await h.emit('connected', { ready: true });
    await h.tick(1000);
    const baseline = { ...h.calls };
    await h.emit('change', { changes: [{ channelId: 'c', chatId: 'one', messageId: 'm1', statusOnly: true, affectsList: false, status: 'read' }] });
    await h.emit('change', { changes: [{ channelId: 'c', chatId: 'one', messageId: 'm2', affectsList: true,
        message: { messageId: 'm2', text: 'incoming' }, chat: { channelId: 'c', chatId: 'one' } }] });
    await h.tick(1500);
    assert.equal(applied.length, 2);
    assert.deepEqual(h.calls, baseline);
    await h.emit('change', { changes: [{ channelId: 'c', chatId: 'one', messageId: 'm3', affectsList: true }] });
    await h.tick(1000);
    assert.equal(h.calls.thread, baseline.thread + 1);
    assert.equal(h.calls.list, baseline.list + 1);
    await h.close();
});

test('a buffered frame from a closing stream cannot update the next account view', async () => {
    const applied = [];
    const h = harness({ onChanges: (changes) => { applied.push(...changes); return { thread: false, list: false }; } });
    await h.flush();
    h.streams.at(-1).emit('event: change\ndata: {"changes":[{"messageId":"old-account-message"}]}\n\n');
    h.options.account = 'potok';
    await h.visible(false);
    assert.equal(applied.length, 0);
    await h.close();
});

test('status refresh only targets selected chat; bursts coalesce and preserve list invalidation', async () => {
    const h = harness(); await h.flush(); await h.emit('connected', { ready: true }); await h.tick(1000);
    assert.equal(h.calls.thread, 1); assert.equal(h.calls.list, 1);
    const event = { channelId: 'c', chatId: 'one', messageId: 'm', affectsList: false };
    for (let i = 0; i < 20; i++) await h.emit('change', { changes: [event] });
    await h.tick(350);
    assert.equal(h.calls.thread, 2); assert.equal(h.calls.list, 1);
    await h.emit('change', { changes: [{ ...event, chatId: 'other' }] }); await h.tick(1000);
    assert.equal(h.calls.thread, 2); assert.equal(h.calls.list, 1);
    await h.emit('change', { changes: [{ ...event, chatId: 'other', affectsList: true }] }); await h.tick(1000);
    assert.equal(h.calls.thread, 2); assert.equal(h.calls.list, 2);
    await h.close(); assert.equal(h.timers.size, 0);
});

test('hidden page closes stream; visible page reconnects and reconciles gaps', async () => {
    const h = harness(); await h.flush(); await h.emit('connected', { ready: true }); await h.tick(1000);
    await h.visible(false);
    assert.equal(h.streams[0].signal.aborted, true); assert.equal(h.output.connection, 'paused');
    assert.equal(h.timers.size, 0);
    await h.visible(true); assert.equal(h.streams.length, 2);
    await h.emit('connected', { ready: true }); await h.tick(1000);
    assert.equal(h.calls.thread, 2); assert.equal(h.calls.list, 2);
    await h.close(); assert.equal(h.timers.size, 0);
});

test('notes reconcile on connection and only unhydrated note events request their own refresh', async () => {
    let noteReads = 0;
    const h = harness({ refreshNotes: async () => { noteReads++; },
        onChanges: (events) => ({ thread: false, list: false,
            notes: events.some((event) => event.kind === 'note' && !event.note) }) });
    await h.flush(); await h.emit('connected', { ready: true }); await h.tick(1000);
    assert.equal(noteReads, 1);
    const baseline = { ...h.calls };
    await h.emit('change', { changes: [{ kind: 'note', note: { id: 'note-1', text: 'Internal only' } }] });
    await h.tick(1000);
    assert.equal(noteReads, 1); assert.deepEqual(h.calls, baseline);
    await h.emit('change', { changes: [{ kind: 'note', noteId: 'note-2' }] });
    await h.tick(350);
    assert.equal(noteReads, 2); assert.deepEqual(h.calls, baseline);
    await h.visible(false); await h.visible(true); await h.emit('connected', { ready: true }); await h.tick(1000);
    assert.equal(noteReads, 3);
    await h.close(); assert.equal(h.timers.size, 0);
});

test('DB outage backs off; a late rejected refresh cannot schedule work after unmount', async () => {
    let reject;
    let attempts = 0;
    const h = harness({ refreshThread: () => { attempts++; return new Promise((_, fail) => { reject = fail; }); } });
    await h.flush(); await h.emit('connected', { ready: true }); await h.tick(350);
    reject(new Error('DB unavailable')); await h.flush(); await h.tick(1000);
    assert.equal(attempts, 1);
    await h.tick(5000); assert.equal(attempts, 2);
    await h.close(); reject(new Error('late failure')); await h.flush();
    assert.equal(h.timers.size, 0);
});

test('stalled stream is aborted and reconnected after heartbeat deadline', async () => {
    const h = harness(); await h.flush(); await h.emit('connected', { ready: true }); await h.tick(72000);
    assert.equal(h.streams[0].signal.aborted, true); assert.equal(h.streams.length, 2);
    await h.close(); assert.equal(h.timers.size, 0);
});
