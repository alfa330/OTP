import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createEmojiLoader, scheduleEmojiWarmup } from '../src/components/wazzup/chatEmojiLoading.js';
import { APPLE_EMOJI_BASE, appleEmojiUrl, createEmojiImageWarmup, handleEmojiImageError, nativeEmojiImage } from '../src/components/wazzup/chatEmojiImages.js';

test('idle, pointer and click share one successful picker download, and a failed download can retry', async () => {
    let calls = 0;
    let finish;
    const load = createEmojiLoader(() => { calls += 1; return new Promise((resolve) => { finish = resolve; }); });
    const idle = load();
    assert.equal(load(), idle);
    assert.equal(load(), idle);
    finish({ default: 'picker' });
    assert.deepEqual(await idle, { default: 'picker' });
    assert.equal(load(), idle);
    assert.equal(calls, 1);
    let retries = 0;
    const retry = createEmojiLoader(async () => { if (retries++ === 0) throw new Error('offline'); return 'loaded'; });
    await assert.rejects(retry, /offline/);
    assert.equal(await retry(), 'loaded');
    assert.equal(retries, 2);
});

const idleHost = (visibility = 'visible') => {
    const queued = new Map();
    const listeners = new Map();
    let next = 0;
    return {
        navigator: {}, queued, listeners,
        document: { visibilityState: visibility,
            addEventListener: (name, listener) => listeners.set(name, listener),
            removeEventListener: (name) => listeners.delete(name) },
        requestIdleCallback: (callback) => { queued.set(++next, callback); return next; },
        cancelIdleCallback: (id) => queued.delete(id),
        flush() { const callbacks = [...queued.values()]; queued.clear(); callbacks.forEach((callback) => callback()); },
    };
};

test('background chats wait for visibility and idle; unmount cancels pending warmup', async () => {
    let loads = 0;
    const host = idleHost('hidden');
    const stop = scheduleEmojiWarmup(() => { loads += 1; }, host);
    assert.equal(host.queued.size, 0);
    host.document.visibilityState = 'visible';
    host.listeners.get('visibilitychange')();
    assert.equal(host.queued.size, 1);
    host.flush();
    await Promise.resolve();
    assert.equal(loads, 1);
    assert.equal(host.listeners.size, 0);
    stop();
    const cancel = scheduleEmojiWarmup(() => { loads += 1; }, host);
    cancel(); host.flush(); await Promise.resolve();
    assert.equal(loads, 1);
});

test('data saver and 2g skip background transfers while unsupported idle API uses a cancellable timer', async () => {
    for (const connection of [{ saveData: true }, { effectiveType: '2g' }, { effectiveType: 'slow-2g' }]) {
        const host = idleHost(); host.navigator.connection = connection;
        scheduleEmojiWarmup(() => assert.fail('must not preload'), host)();
        assert.equal(host.queued.size, 0);
        assert.equal(host.listeners.size, 0);
    }
    const host = idleHost();
    delete host.requestIdleCallback;
    let delay;
    let cleared;
    host.setTimeout = (callback, ms) => { delay = ms; return 7; };
    host.clearTimeout = (id) => { cleared = id; };
    scheduleEmojiWarmup(() => {}, host)();
    assert.equal(delay, 1200); assert.equal(cleared, 7);
});

test('first-screen image warmup is capped at 24 unique PNGs, four in flight, once per session', () => {
    const previous = globalThis.Image;
    globalThis.Image = function ImageFixture() {};
    let active = 0;
    let maxActive = 0;
    const images = [];
    const warm = createEmojiImageWarmup(Array.from({ length: 50 }, (_, i) => (0x1f600 + i).toString(16)), () => {
        active += 1; maxActive = Math.max(maxActive, active);
        const image = { finish() { active -= 1; this.onload(); } };
        images.push(image); return image;
    });
    try {
        warm(); warm();
        assert.equal(images.length, 4);
        for (let i = 0; i < images.length; i += 1) images[i].finish();
        assert.equal(images.length, 24);
        assert.equal(maxActive, 4);
        assert.equal(active, 0);
        assert.ok(images.every((img) => img.src.startsWith(APPLE_EMOJI_BASE) && img.fetchPriority === 'low'));
        warm(); assert.equal(images.length, 24);
    } finally { if (previous === undefined) delete globalThis.Image; else globalThis.Image = previous; }
});

test('Apple PNGs are versioned and failed image retains the exact Unicode including skin tone', () => {
    const unified = '1f44b-1f3fd';
    assert.equal(appleEmojiUrl(unified), `${APPLE_EMOJI_BASE}${unified}.png`);
    let stopped = 0;
    const target = { tagName: 'IMG', src: appleEmojiUrl(unified) };
    handleEmojiImageError({ target, stopPropagation: () => { stopped += 1; } });
    assert.equal(stopped, 1);
    assert.match(decodeURIComponent(target.src), /👋🏽/u);
    assert.equal(appleEmojiUrl(unified), target.src);
    handleEmojiImageError({ target, stopPropagation: () => { stopped += 1; } });
    assert.equal(stopped, 1, 'fallback does not loop on repeated image errors');
    assert.equal(nativeEmojiImage('../../x'), '');
    assert.equal(nativeEmojiImage('110000'), '');
    assert.equal(nativeEmojiImage('d800'), '');
    assert.match(decodeURIComponent(nativeEmojiImage('003c')), /&lt;/);
});

const require = createRequire(import.meta.url);
const React = require('react');
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'ChatEmojiPickerTest.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/ChatEmojiPicker.jsx')],
    outfile: output, bundle: true, format: 'esm', platform: 'node', external: ['react'],
    plugins: [{ name: 'emoji-vendor-fixture', setup(build) {
        build.onResolve({ filter: /^emoji-picker-react$/ }, () => ({ path: 'picker', namespace: 'emoji-fixture' }));
        build.onLoad({ filter: /.*/, namespace: 'emoji-fixture' }, () => ({ contents: 'export default function FixturePicker(){return null}', loader: 'js' }));
    } }],
});
const { default: ChatEmojiPicker } = await import(pathToFileURL(output));
test('picker renders Apple artwork lazily while insert callback returns Unicode rather than an image URL', () => {
    const selected = [];
    const wrapper = ChatEmojiPicker({ onSelect: (emoji) => selected.push(emoji) });
    const picker = React.Children.only(wrapper.props.children);
    assert.equal(picker.props.emojiStyle, 'apple');
    assert.equal(picker.props.lazyLoadEmojis, true);
    assert.equal(picker.props.width, '100%');
    picker.props.onEmojiClick({ emoji: '👩🏽‍💻', imageUrl: 'https://images.invalid/wrong.png' });
    assert.deepEqual(selected, ['👩🏽‍💻']);
});

const toolsOutput = join(cache, 'ChatComposerToolsEmojiTest.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/ChatComposerTools.jsx')],
    outfile: toolsOutput, bundle: true, format: 'esm', platform: 'node', external: ['lucide-react', 'axios'],
    plugins: [{ name: 'emoji-tools-harness', setup(build) {
        build.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'tools-fixture' }));
        build.onResolve({ filter: /chatEmojiLoading$/ }, () => ({ path: 'loading', namespace: 'tools-fixture' }));
        build.onLoad({ filter: /.*/, namespace: 'tools-fixture' }, ({ path }) => ({ contents: path === 'react'
            ? 'const h=()=>globalThis.__emojiToolsHarness; export const useState=(...x)=>h().useState(...x); export const useEffect=(...x)=>h().useEffect(...x); export default {createElement:(...x)=>h().createElement(...x)};'
            : 'export const loadChatEmojiPicker=()=>globalThis.__emojiToolsHarness.load(); export const scheduleEmojiWarmup=()=>()=>{}; export const warmChatEmojiPicker=loadChatEmojiPicker;', loader: 'js' }));
    } }],
});
const { default: ChatComposerTools } = await import(pathToFileURL(toolsOutput));
const find = (root, predicate) => {
    if (!root || typeof root !== 'object') return null;
    if (predicate(root)) return root;
    for (const child of React.Children.toArray(root.props?.children)) { const match = find(child, predicate); if (match) return match; }
    return null;
};
test('closing and reopening emoji preserves its mounted picker and does not reload the module', async () => {
    const slots = [];
    let index = 0;
    let loads = 0;
    let effects = [];
    function PickerFixture() { return null; }
    const harness = {
        createElement: React.createElement,
        useState(initial) { const i = index++; slots[i] ??= { value: initial }; return [slots[i].value, (v) => { slots[i].value = typeof v === 'function' ? v(slots[i].value) : v; }]; },
        useEffect(effect, deps) {
            const i = index++; const old = slots[i];
            if (old && deps.every((v, k) => Object.is(v, old.deps[k]))) return;
            slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); });
        },
        async load() { loads += 1; return { default: PickerFixture }; },
        render() {
            index = 0;
            const tree = ChatComposerTools({ apiBaseUrl: '/test', channelId: 'test', locked: false, slash: null, onEmoji: () => {} });
            const run = effects; effects = []; run.forEach((fn) => fn()); return tree;
        },
    };
    globalThis.__emojiToolsHarness = harness;
    try {
        let tree = harness.render();
        const button = (tree) => find(tree, (el) => el.props?.['aria-label'] === 'Выбрать эмодзи');
        button(tree).props.onClick();
        harness.render(); await Promise.resolve();
        tree = harness.render();
        const picker = find(tree, (el) => el.type === PickerFixture);
        assert.ok(picker);
        button(tree).props.onClick(); tree = harness.render();
        assert.equal(find(tree, (el) => el.props?.role === 'dialog').props.hidden, true);
        assert.equal(find(tree, (el) => el.type === PickerFixture).type, picker.type);
        button(tree).props.onClick(); tree = harness.render();
        assert.equal(find(tree, (el) => el.props?.role === 'dialog').props.hidden, undefined);
        assert.equal(find(tree, (el) => el.type === PickerFixture).type, picker.type);
        assert.equal(loads, 1);
    } finally { slots.forEach((s) => s.cleanup?.()); delete globalThis.__emojiToolsHarness; }
});
