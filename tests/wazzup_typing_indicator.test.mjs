import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import React from 'react';
import { build } from 'esbuild';
import { createChatTypingStore } from '../src/components/wazzup/chatTypingPresence.js';
import { pilotChatKey } from '../src/components/wazzup/chatPilot.js';

// Execute the production component and store. The fixture commits the changed
// viewport height before layout effects, as a browser does when the rail appears.
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzup-typing-indicator.mjs');
await build({ entryPoints: ['src/components/wazzup/ChatTypingIndicator.jsx'], outfile: output,
    bundle: true, format: 'esm', platform: 'node', plugins: [{ name: 'indicator-react', setup(builder) {
        builder.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'fixture' }));
        builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: `
            const h=()=>globalThis.__typingIndicator;
            export const useRef=(...v)=>h().useRef(...v);
            export const useCallback=(...v)=>h().useCallback(...v);
            export const useLayoutEffect=(...v)=>h().useLayoutEffect(...v);
            export const useSyncExternalStore=(...v)=>h().useSyncExternalStore(...v);
            export default {Fragment:Symbol.for('react.fragment'),createElement:(...v)=>h().createElement(...v)};
        ` }));
    } }] });
const { default: Indicator } = await import(pathToFileURL(output));

const chat = { channelId: 'channel-a', chatId: 'chat-a' };
const key = pilotChatKey('op', chat);
const event = (updates = {}) => ({ kind: 'typing', account: 'op', ...chat, userId: '2',
    authorName: 'Анна', clientId: 'tab-a', typing: true, emittedAt: 1000, expiresAt: 9000,
    sequence: 1, ...updates });
const elements = (node) => Array.isArray(node) ? node.flatMap(elements)
    : React.isValidElement(node) ? [node, ...elements(node.props.children)] : [];
const text = (node) => Array.isArray(node) ? node.map(text).join('')
    : React.isValidElement(node) ? text(node.props.children) : typeof node === 'string' ? node : '';
const sameDeps = (a, b) => a?.length === b?.length && a.every((value, i) => Object.is(value, b[i]));

function fixture({ scrollTop = 400, bubble = true } = {}) {
    const slots = [], timers = new Map(), subscriptions = new Set();
    let cursor = 0, timerId = 0, dirty = true, effects = [], tree;
    let viewportHeight = 600, top = scrollTop;
    const scrollWrites = [];
    const box = { scrollHeight: 1000, get clientHeight() { return viewportHeight; },
        get scrollTop() { return top; }, set scrollTop(value) {
            scrollWrites.push(value); top = Math.max(0, Math.min(value, 1000 - viewportHeight));
        } };
    const store = createChatTypingStore({ ownerId: 1, now: () => 1000,
        schedule: (fn) => { timers.set(++timerId, fn); return timerId; }, cancel: (id) => timers.delete(id) });
    const trackedStore = { ...store, subscribe(chatKey, listener) {
        const stop = store.subscribe(chatKey, () => { h.notifications++; listener(); });
        subscriptions.add(stop);
        return () => { subscriptions.delete(stop); stop(); };
    } };
    const props = { store: trackedStore, chatKey: key, bubble, threadBox: { current: box },
        fallback: React.createElement('span', null, 'Последнее сообщение') };
    const h = {
        store, box, scrollWrites, notifications: 0, renders: 0, createElement: React.createElement,
        useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial }; },
        useCallback(callback, deps) {
            const index = cursor++, old = slots[index];
            if (old && sameDeps(old.deps, deps)) return old.callback;
            slots[index] = { deps, callback }; return callback;
        },
        useLayoutEffect(effect, deps) {
            const index = cursor++, old = slots[index];
            if (old && sameDeps(old.deps, deps)) return;
            slots[index] = { deps };
            effects.push(() => { old?.cleanup?.(); slots[index].cleanup = effect(); });
        },
        useSyncExternalStore(subscribe, snapshot) {
            const index = cursor++;
            let slot = slots[index];
            if (!slot || slot.subscribe !== subscribe) {
                slot?.cleanup?.();
                slot = slots[index] = { subscribe, value: snapshot() };
                slot.cleanup = subscribe(() => {
                    const next = slot.snapshot();
                    if (!Object.is(next, slot.value)) { slot.value = next; dirty = true; }
                });
            }
            slot.snapshot = snapshot; slot.value = snapshot();
            return slot.value;
        },
        render(next) {
            if (next) { Object.assign(props, next); dirty = true; }
            if (!dirty) return tree;
            globalThis.__typingIndicator = h; cursor = 0; dirty = false; h.renders++;
            tree = Indicator(props);
            const hasRail = elements(tree).some((node) => node.props['data-testid'] === 'wazzup-thread-typing'
                && node.props.className !== 'sr-only');
            viewportHeight = hasRail ? 560 : 600;
            top = Math.min(top, box.scrollHeight - viewportHeight);
            const pending = effects; effects = []; pending.forEach((effect) => effect());
            return tree;
        },
        apply(events) { store.apply(events); return h.render(); },
        close() {
            slots.forEach((slot) => slot?.cleanup?.()); store.clear();
            assert.equal(subscriptions.size, 0);
            assert.equal(timers.size, 0);
            delete globalThis.__typingIndicator;
        },
    };
    h.render(); return h;
}

test('thread bubble names the operator and has exactly three decorative animated dots', () => {
    const h = fixture();
    try {
        assert.equal(h.render().props.className, 'sr-only');
        assert.equal(elements(h.render()).filter((node) => node.props.className === 'wazzup-typing-dot').length, 0);
        const tree = h.apply([event()]);
        assert.equal(tree.props.role, 'status'); assert.equal(tree.props['aria-live'], 'polite');
        assert.match(tree.props.className, /justify-end/);
        assert.equal(elements(tree).filter((node) => node.props.className === 'wazzup-typing-dot').length, 3);
        const visual = elements(tree).find((node) => node.props['aria-hidden'] === 'true');
        assert.equal(text(visual), 'Анна печатает');
        assert.equal(visual.props.title, 'Анна печатает…');
        assert.match(visual.props.className, /wazzup-message-outgoing/);
        assert.equal(text(elements(tree).find((node) => node.type === 'span' && node.props.className === 'sr-only')),
            'Анна печатает…');
    } finally { h.close(); }
});

test('unrelated chats and heartbeat extensions do not notify or render the indicator', () => {
    const h = fixture();
    try {
        h.apply([event({ chatId: 'other-chat' })]);
        assert.equal(h.notifications, 0); assert.equal(h.renders, 1);
        h.apply([event()]);
        assert.equal(h.notifications, 1); assert.equal(h.renders, 2);
        h.apply([event({ sequence: 2, emittedAt: 2000, expiresAt: 10000 })]);
        assert.equal(h.notifications, 1); assert.equal(h.renders, 2);
        h.apply([event({ sequence: 3, typing: false })]);
        assert.equal(h.notifications, 2); assert.equal(h.renders, 3);
    } finally { h.close(); }
});

test('showing the typing rail keeps the newest message visible when the reader was at the bottom', () => {
    const h = fixture();
    try {
        assert.equal(h.box.scrollTop, 400); assert.equal(h.scrollWrites.length, 0);
        h.apply([event()]);
        assert.equal(h.box.clientHeight, 560);
        assert.equal(h.box.scrollTop, 440);
        assert.equal(h.box.scrollHeight - h.box.scrollTop - h.box.clientHeight, 0);
        h.apply([event({ sequence: 2, typing: false })]);
        assert.equal(h.box.clientHeight, 600); assert.equal(h.box.scrollTop, 400);
    } finally { h.close(); }
});

test('typing starts and stops never move a reader browsing earlier messages', () => {
    const h = fixture({ scrollTop: 120 });
    try {
        h.apply([event()]); assert.equal(h.box.scrollTop, 120);
        h.apply([event({ sequence: 2, typing: false })]); assert.equal(h.box.scrollTop, 120);
        assert.deepEqual(h.scrollWrites, []);
    } finally { h.close(); }
});

test('list variant restores the last message after typing stops', () => {
    const h = fixture({ bubble: false });
    try {
        assert.equal(text(h.render()), 'Последнее сообщение');
        const tree = h.apply([event()]);
        assert.equal(text(tree), 'Анна печатает');
        const row = elements(tree).find((node) => node.props['data-testid'] === 'wazzup-chat-typing');
        assert.equal(row.props.title, 'Анна печатает…');
        // As in the thread: three decorative dots after the verb.
        const dots = elements(tree).filter((node) => node.props.className === 'wazzup-typing-dot');
        assert.equal(dots.length, 3);
        assert.equal(elements(tree).find((node) => node.props['aria-hidden'] === 'true')
            .props.className.includes('shrink-0'), true);
        // A long name is truncated; the verb and the dots never are.
        assert.deepEqual(elements(tree).filter((node) => /^shrink-0/.test(node.props.className || ''))
            .map(text), [' печатает']);
        assert.equal(text(h.apply([event({ sequence: 2, typing: false })])), 'Последнее сообщение');
    } finally { h.close(); }
});
