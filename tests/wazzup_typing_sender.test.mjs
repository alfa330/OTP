import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import React from 'react';
import { build } from 'esbuild';
import { createChatTypingSender } from '../src/components/wazzup/chatTypingSender.js';

const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
function clock() {
    let time = 0, id = 0;
    const timers = new Map();
    return {
        now: () => time,
        schedule(fn, delay) { timers.set(++id, { at: time + delay, fn }); return id; },
        cancel(timer) { timers.delete(timer); },
        get pending() { return timers.size; },
        async tick(ms) {
            const end = time + ms;
            for (;;) {
                const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
                if (!next) break;
                time = next[1].at; timers.delete(next[0]); next[1].fn(); await flush();
            }
            time = end; await flush();
        },
    };
}
function senderFixture({ controlled = false } = {}) {
    const time = clock(), calls = [];
    const sender = createChatTypingSender({ ...time, send(state) {
        const call = { ...state, at: time.now() };
        calls.push(call);
        return controlled ? new Promise((resolve, reject) => Object.assign(call, { resolve, reject })) : Promise.resolve();
    } });
    return { time, calls, sender };
}

test('first input is immediate; hundreds of edits send at most one positive every three seconds', async () => {
    const { sender, time, calls } = senderFixture();
    sender.activity('private draft');
    assert.deepEqual(calls.map(({ typing, at }) => [typing, at]), [[true, 0]]);
    for (let i = 1; i < 500; i += 1) { await time.tick(20); sender.activity(`private ${i}`); }
    assert.deepEqual(calls.map(({ typing, at }) => [typing, at]), [[true, 0], [true, 3000], [true, 6000], [true, 9000]]);
    assert.ok(calls.every((call) => Object.keys(call).sort().join(',') === 'at,sequence,typing'), 'draft text never leaves the state machine');
    sender.destroy(); await flush();
    assert.equal(calls.at(-1).typing, false);
    assert.equal(time.pending, 0);
});

test('idle stop is measured from the last edit; a saved nonempty field never renews itself', async () => {
    const { sender, time, calls } = senderFixture();
    assert.equal(time.pending, 0);
    await time.tick(60000); assert.equal(calls.length, 0);
    sender.activity('a'); await flush();
    await time.tick(2499); assert.equal(calls.length, 1);
    await time.tick(1); assert.deepEqual(calls.map((call) => call.typing), [true, false]);
    assert.equal(time.pending, 0);
    await time.tick(60000); assert.equal(calls.length, 2);
});

test('clear cancels a queued pulse; restarting after a stop is held to one start per second', async () => {
    const { sender, time, calls } = senderFixture();
    sender.activity('a'); await flush();
    await time.tick(150); sender.activity('ab');
    await time.tick(150); sender.activity('   '); await flush();
    assert.deepEqual(calls.map((call) => call.typing), [true, false]);
    assert.equal(time.pending, 0);
    await time.tick(100); sender.activity('new'); await time.tick(599); assert.equal(calls.length, 2);
    await time.tick(1); assert.deepEqual([calls.at(-1).typing, calls.at(-1).at], [true, 1000]);
    sender.destroy(); await flush();
});

test('a pause that ends in the idle stop does not hold back the next start', async () => {
    // Found by review: a timer heartbeat right before the idle stop used to delay
    // the restart by up to 3 s while the operator was typing again.
    const { sender, time, calls } = senderFixture();
    sender.activity('a'); await flush();
    await time.tick(600); sender.activity('ab');
    await time.tick(2600);
    sender.activity('abc'); await flush();
    assert.deepEqual(calls.map(({ typing, at }) => [typing, at]), [[true, 0], [false, 3100], [true, 3200]]);
    for (let i = 1; i <= 40; i += 1) { await time.tick(200); sender.activity(`abc${i}`); }
    const positives = calls.filter((call) => call.typing).map((call) => call.at);
    assert.deepEqual(positives, [0, 3200, 6200, 9200], 'a live indicator is renewed by edits every 3 s');
    sender.destroy(); await flush();
});

test('stop waits for the outstanding start and never sends a stale queued pulse afterward', async () => {
    const { sender, time, calls } = senderFixture({ controlled: true });
    sender.activity('a');
    await time.tick(3500); sender.activity('ab'); sender.stop();
    assert.equal(calls.length, 1, 'one request in flight');
    calls[0].resolve(); await flush();
    assert.deepEqual(calls.map(({ typing, sequence }) => [typing, sequence]), [[true, 1], [false, 2]]);
    calls[1].resolve(); await flush();
    await time.tick(30000); assert.equal(calls.length, 2);
    assert.equal(time.pending, 0);
});

test('unmount finishes one ordered stop even when a start fails after unmount', async () => {
    const { sender, time, calls } = senderFixture({ controlled: true });
    sender.activity('a'); sender.destroy(); sender.activity('late');
    calls[0].reject(new Error('network timeout')); await flush();
    assert.deepEqual(calls.map((call) => call.typing), [true, false]);
    calls[1].reject(new Error('offline')); await flush();
    await time.tick(60000); assert.equal(calls.length, 2);
    assert.equal(time.pending, 0);
});

test('network failures back off during continued edits and stop does not retry endlessly', async () => {
    const { sender, time, calls } = senderFixture({ controlled: true });
    sender.activity('a'); calls[0].reject(new Error('offline')); await flush();
    for (let i = 1; i < 5; i += 1) { await time.tick(1000); sender.activity(`a${i}`); }
    assert.equal(calls.length, 1);
    await time.tick(1000);
    assert.equal(calls.length, 2); assert.equal(calls[1].at, 5000);
    sender.stop(); calls[1].reject(new Error('offline')); await flush();
    calls[2].reject(new Error('offline')); await flush();
    await time.tick(60000); assert.equal(calls.length, 3);
    assert.equal(time.pending, 0);
});

test('permission failure silences the sender for the rest of its session', async () => {
    const { sender, time, calls } = senderFixture({ controlled: true });
    sender.activity('a'); calls[0].reject({ response: { status: 403 } }); await flush();
    for (let i = 0; i < 5; i += 1) { await time.tick(5000); sender.activity('b'); }
    sender.destroy(); assert.equal(calls.length, 1); assert.equal(time.pending, 0);
});

// Run actual composer handlers and hook effects with controlled browser events.
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'ChatTypingComposer.mjs');
await build({
    entryPoints: ['src/components/wazzup/ChatPilotComposer.jsx'], outfile: output,
    bundle: true, format: 'esm', platform: 'node', external: ['lucide-react'],
    plugins: [{ name: 'typing-composer-fixture', setup(builder) {
        builder.onResolve({ filter: /^(react|axios)$/ }, ({ path }) => ({ path, namespace: 'typing-fixture' }));
        builder.onLoad({ filter: /.*/, namespace: 'typing-fixture' }, ({ path }) => ({ loader: 'js', contents: path === 'react'
            ? `const h=()=>globalThis.__typingComposer;
                export const useState=(...x)=>h().useState(...x),useRef=(...x)=>h().useRef(...x),useEffect=(...x)=>h().useEffect(...x);
                export const useSyncExternalStore=(subscribe,get)=>get(),memo=(component)=>component,lazy=()=>()=>null,Suspense=({children})=>children;
                export default {createElement:(...x)=>h().createElement(...x)};`
            : `export default {post:(...x)=>globalThis.__typingComposer.post(...x)};` }));
    } }],
});
const { default: Composer } = await import(pathToFileURL(output));
const find = (node, check) => {
    if (!node || typeof node !== 'object') return null;
    if (check(node)) return node;
    for (const child of React.Children.toArray(node.props?.children)) { const match = find(child, check); if (match) return match; }
    return null;
};
const eventTarget = () => {
    const listeners = new Map();
    return {
        addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
        removeEventListener(name, callback) { listeners.get(name)?.delete(callback); },
        fire(name) { for (const callback of listeners.get(name) || []) callback(); },
        count: () => [...listeners.values()].reduce((total, callbacks) => total + callbacks.size, 0),
    };
};
function composerFixture({ enabled = true, saved = '' } = {}) {
    const savedGlobals = Object.fromEntries(['document', 'window', 'sessionStorage', 'localStorage', 'setTimeout', 'clearTimeout']
        .map((key) => [key, globalThis[key]]));
    const previousNow = Date.now;
    const time = clock(), requests = [], sent = [], store = new Map();
    const doc = { ...eventTarget(), hidden: false }, win = eventTarget();
    const storage = { getItem: (key) => store.get(key) || null, setItem: (key, value) => store.set(key, value),
        removeItem: (key) => store.delete(key), key: (index) => [...store.keys()][index] ?? null, get length() { return store.size; } };
    Object.assign(globalThis, { document: doc, window: win, sessionStorage: storage, localStorage: storage,
        setTimeout: time.schedule, clearTimeout: time.cancel });
    Date.now = () => 100000 + time.now();
    const props = { apiBaseUrl: '/fixture', headers: () => ({ Authorization: 'test-token' }), typingEnabled: enabled,
        chat: { channelId: 'channel-a', chatId: 'chat-a' }, onSend: (message) => sent.push(message) };
    if (saved) store.set('icore.wazzup.pilot.draft.["op","channel-a","chat-a"]', JSON.stringify({ text: saved }));
    let slots = [], cursor = 0, effects = [], previousKey = null;
    const h = {
        time, requests, sent, doc, win,
        createElement: React.createElement,
        post(url, body, config) { requests.push({ url, body, config }); return Promise.resolve({ data: {} }); },
        useState(initial) { const i = cursor++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
            return [slots[i].value, (value) => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }]; },
        useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
        useEffect(effect, deps) { const i = cursor++, old = slots[i];
            if (old && deps?.every((value, k) => Object.is(value, old.deps?.[k]))) return;
            slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); }); },
        render(next = {}) {
            Object.assign(props, next);
            const wrapper = Composer(props);
            if (previousKey !== null && wrapper.key !== previousKey) { h.unmount(); slots = []; }
            previousKey = wrapper.key; cursor = 0;
            const tree = wrapper.type(wrapper.props);
            const pending = effects; effects = []; pending.forEach((effect) => effect());
            return tree;
        },
        field() { return find(h.render(), (node) => node.type === 'textarea'); },
        edit(value) { h.field().props.onChange({ target: { value } }); },
        unmount() { slots.forEach((slot) => { slot?.cleanup?.(); if (slot) slot.cleanup = null; }); },
        restore() { h.unmount(); Object.assign(globalThis, savedGlobals); Date.now = previousNow; delete globalThis.__typingComposer; },
    };
    globalThis.__typingComposer = h;
    return h;
}

test('opening a saved draft or a disabled composer sends no presence; actual input contains no text or name', async () => {
    const h = composerFixture({ saved: 'Saved confidential text' });
    try {
        assert.equal(h.field().props.value, 'Saved confidential text');
        await h.time.tick(30000); assert.equal(h.requests.length, 0);
        h.edit('Actual confidential text'); await flush();
        const { url, body, config } = h.requests[0];
        assert.equal(url, '/fixture/api/wazzup/pilot/typing');
        assert.deepEqual(Object.keys(body).sort(), ['account', 'channelId', 'chatId', 'clientId', 'sequence', 'typing']);
        assert.match(body.clientId, /^[0-9a-f-]{36}$/); assert.equal(body.typing, true);
        assert.equal(config.timeout, 2500); assert.equal(config.headers.Authorization, 'test-token');
        h.render({ typingEnabled: false }); await flush();
        assert.equal(h.requests.at(-1).body.typing, false);
        h.edit('Disabled'); await h.time.tick(30000); assert.equal(h.requests.length, 2);
    } finally { h.restore(); }
});

test('composer blur, clear and accepted send stop typing without waiting for message delivery', async () => {
    const h = composerFixture();
    try {
        h.edit('one'); await flush(); h.field().props.onBlur(); await flush();
        assert.deepEqual(h.requests.map((request) => request.body.typing), [true, false]);
        await h.time.tick(3000); h.edit('two'); await flush(); h.edit(''); await flush();
        assert.deepEqual(h.requests.map((request) => request.body.typing), [true, false, true, false]);
        await h.time.tick(3000); h.edit('three'); await flush(); h.render().props.onSubmit(); await flush();
        assert.equal(h.sent[0].text, 'three'); assert.equal(h.field().props.value, '');
        assert.deepEqual(h.requests.map((request) => request.body.typing), [true, false, true, false, true, false]);
        assert.equal(h.time.pending, 0);
    } finally { h.restore(); }
});

test('hidden tab stops typing and stays silent until a fresh visible edit', async () => {
    const h = composerFixture();
    try {
        h.edit('one'); await flush(); h.doc.hidden = true; h.doc.fire('visibilitychange'); await flush();
        h.edit('hidden'); await h.time.tick(30000); assert.equal(h.requests.length, 2);
        h.doc.hidden = false; h.doc.fire('visibilitychange'); await flush(); assert.equal(h.requests.length, 2);
        h.edit('visible'); await flush(); assert.equal(h.requests.length, 3);
        h.win.fire('pagehide'); await flush(); assert.equal(h.requests.at(-1).body.typing, false);
    } finally { h.restore(); }
});

test('chat switch stops the previous chat, isolates session identity, and unmount removes listeners', async () => {
    const h = composerFixture();
    try {
        h.edit('one'); await flush();
        h.render({ chat: { channelId: 'channel-b', chatId: 'chat-b' } }); await flush();
        assert.deepEqual(h.requests.map(({ body }) => [body.chatId, body.typing]), [['chat-a', true], ['chat-a', false]]);
        h.edit('two'); await flush();
        assert.equal(h.requests[2].body.chatId, 'chat-b');
        assert.notEqual(h.requests[0].body.clientId, h.requests[2].body.clientId);
        assert.equal(h.requests[2].body.sequence, 1);
        h.unmount(); await flush();
        assert.equal(h.requests.at(-1).body.typing, false);
        assert.equal(h.doc.count() + h.win.count(), 0); assert.equal(h.time.pending, 0);
    } finally { h.restore(); }
});
