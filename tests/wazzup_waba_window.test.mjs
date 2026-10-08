import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'WazzupWabaWindow.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/useWabaWindowExpired.js')],
    outfile: output, bundle: true, format: 'esm', platform: 'node', target: 'node22',
    plugins: [{ name: 'waba-window-harness', setup(builder) {
        builder.onResolve({ filter: /^react$/ }, ({ path }) => ({ path, namespace: 'fixture' }));
        builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ loader: 'js', contents:
            'export const useState=(...args)=>globalThis.__wabaWindowHarness.useState(...args); export const useEffect=(...args)=>globalThis.__wabaWindowHarness.useEffect(...args);',
        }));
    } }],
});
const { latestInboundTime, useWabaWindowExpired, WABA_WINDOW_MS } = await import(pathToFileURL(output));

function fixture(initialNow = Date.parse('2026-10-08T12:00:00Z')) {
    const previous = { now: Date.now, timeout: globalThis.setTimeout, clear: globalThis.clearTimeout,
        window: globalThis.window, document: globalThis.document };
    let now = initialNow, id = 0, index = 0, lateWrites = 0, unmounted = false;
    let effects = [];
    const timers = new Map(), focus = new Set(), visibility = new Set(), slots = [];
    Date.now = () => now;
    globalThis.setTimeout = (callback, delay) => { timers.set(++id, { callback, delay, at: now + delay }); return id; };
    globalThis.clearTimeout = (timer) => timers.delete(timer);
    globalThis.window = { addEventListener: (_type, fn) => focus.add(fn), removeEventListener: (_type, fn) => focus.delete(fn) };
    globalThis.document = { visibilityState: 'visible', addEventListener: (_type, fn) => visibility.add(fn), removeEventListener: (_type, fn) => visibility.delete(fn) };
    const harness = {
        useState(initial) {
            const slot = index++;
            slots[slot] ??= { value: typeof initial === 'function' ? initial() : initial };
            return [slots[slot].value, (value) => {
                if (unmounted) lateWrites++;
                slots[slot].value = typeof value === 'function' ? value(slots[slot].value) : value;
            }];
        },
        useEffect(fn, deps) {
            const slot = index++, old = slots[slot];
            if (old && deps.every((value, offset) => Object.is(value, old.deps[offset]))) return;
            slots[slot] = { deps };
            effects.push(() => { old?.cleanup?.(); slots[slot].cleanup = fn(); });
        },
        render(enabled, inbound) {
            index = 0;
            const result = useWabaWindowExpired(enabled, inbound);
            const pending = effects; effects = []; pending.forEach((fn) => fn());
            return result;
        },
        advance(amount) {
            now += amount;
            for (const [key, timer] of [...timers]) if (timer.at <= now) { timers.delete(key); timer.callback(); }
        },
        wakeFocus() { focus.forEach((fn) => fn()); },
        wakeVisible(state = 'visible') { document.visibilityState = state; visibility.forEach((fn) => fn()); },
        unmount() { slots.forEach((slot) => slot?.cleanup?.()); unmounted = true; },
        restore() {
            if (!unmounted) this.unmount();
            Date.now = previous.now; globalThis.setTimeout = previous.timeout; globalThis.clearTimeout = previous.clear;
            globalThis.window = previous.window; globalThis.document = previous.document; delete globalThis.__wabaWindowHarness;
        },
        timers, focus, visibility,
        get now() { return now; }, set now(value) { now = value; },
        get lateWrites() { return lateWrites; },
    };
    globalThis.__wabaWindowHarness = harness;
    return harness;
}

test('latest incoming time uses original timestamps and baseline, excluding operators and internal rows', () => {
    const base = Date.parse('2026-10-01T10:00:00Z');
    assert.equal(latestInboundTime([
        { isEcho: false, dt: base + 100 },
        { isEcho: true, dt: base + 900 },
        { dt: base + 800 },
        { isEcho: false, _note: true, dt: base + 700 },
        { isEcho: false, _day: true, dt: base + 600 },
        { isEcho: false, wazzupDt: base + 200, dt: base + 1000 },
        { isEcho: false, isDeleted: true, dt: base + 300 },
        { isEcho: false, dt: 'invalid' },
    ], base), base + 300);
    assert.equal(latestInboundTime([{ isEcho: false, dt: base - 100 }], new Date(base).toISOString()), base);
    assert.equal(latestInboundTime([], null), null);
    assert.equal(latestInboundTime([{ isEcho: false, dt: 0 }]), 0);
});

test('missing, invalid and disabled channel timestamps show no hint or timer', () => {
    const h = fixture();
    try {
        for (const value of [null, undefined, '', ' ', 'invalid', NaN, Infinity, true, {}]) {
            assert.equal(h.render(true, value), false); assert.equal(h.timers.size, 0);
        }
        assert.equal(h.render(false, h.now - WABA_WINDOW_MS * 2), false);
        assert.equal(h.timers.size, 0); assert.equal(h.focus.size, 0); assert.equal(h.visibility.size, 0);
    } finally { h.restore(); }
});

test('one timeout expires precisely after 24 hours with no polling', () => {
    const h = fixture(); const inbound = h.now;
    try {
        assert.equal(h.render(true, inbound), false);
        assert.equal(h.timers.size, 1); assert.equal([...h.timers.values()][0].delay, WABA_WINDOW_MS);
        h.advance(WABA_WINDOW_MS - 1); assert.equal(h.render(true, inbound), false);
        assert.equal(h.timers.size, 1);
        h.advance(1); assert.equal(h.render(true, inbound), true); assert.equal(h.timers.size, 0);
    } finally { h.restore(); }
});

test('new incoming message resets an expired window and switching channel clears timer and listeners', () => {
    const h = fixture();
    try {
        assert.equal(h.render(true, h.now - WABA_WINDOW_MS), true);
        const inbound = h.now;
        assert.equal(h.render(true, inbound), false); assert.equal(h.timers.size, 1);
        h.advance(5000);
        const nextInbound = h.now;
        assert.equal(h.render(true, nextInbound), false); assert.equal(h.timers.size, 1);
        assert.equal([...h.timers.values()][0].delay, WABA_WINDOW_MS);
        assert.equal(h.render(false, nextInbound), false);
        assert.equal(h.timers.size, 0); assert.equal(h.focus.size, 0); assert.equal(h.visibility.size, 0);
    } finally { h.restore(); }
});

test('focus and visible wake reevaluate a suspended clock without accumulating timers', () => {
    const h = fixture(); const inbound = h.now;
    try {
        h.render(true, inbound); h.wakeFocus(); h.wakeVisible(); assert.equal(h.timers.size, 1);
        h.now += WABA_WINDOW_MS;
        h.wakeVisible('hidden'); assert.equal(h.timers.size, 1);
        h.wakeVisible(); assert.equal(h.timers.size, 0); assert.equal(h.render(true, inbound), true);
        h.now = inbound + 1000; h.wakeFocus(); assert.equal(h.render(true, inbound), false);
        assert.equal(h.timers.size, 1); assert.equal([...h.timers.values()][0].delay, WABA_WINDOW_MS - 1000);
    } finally { h.restore(); }
});

test('future times stay open until their own deadline and long timers are capped', () => {
    const h = fixture(); const inbound = h.now + 30 * WABA_WINDOW_MS;
    try {
        assert.equal(h.render(true, inbound), false);
        assert.equal([...h.timers.values()][0].delay, 2 ** 31 - 1);
        h.advance(2 ** 31 - 1); assert.equal(h.render(true, inbound), false); assert.equal(h.timers.size, 1);
        h.now = inbound + WABA_WINDOW_MS; h.wakeFocus(); assert.equal(h.render(true, inbound), true);
    } finally { h.restore(); }
});

test('unmount removes timer/listeners and already queued callbacks cannot write state', () => {
    const h = fixture();
    try {
        h.render(true, h.now); const late = [...h.timers.values()][0].callback;
        h.unmount(); assert.equal(h.timers.size, 0); assert.equal(h.focus.size, 0); assert.equal(h.visibility.size, 0);
        late(); assert.equal(h.lateWrites, 0);
    } finally { h.restore(); }
});
