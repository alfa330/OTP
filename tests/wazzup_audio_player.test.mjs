import test, { mock } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { build } from 'esbuild';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzup-audio-player.mjs');
await build({ entryPoints: ['src/components/wazzup/ChatAudioPlayer.jsx'], outfile: output,
    bundle: true, format: 'esm', platform: 'node', external: ['lucide-react'], loader: { '.css': 'empty' },
    plugins: [{ name: 'audio-hooks', setup(builder) {
        builder.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'fixture' }));
        builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: `
            const h=()=>globalThis.__audioHarness;
            export const useState=(...v)=>h().useState(...v);
            export const useRef=(...v)=>h().useRef(...v);
            export const useEffect=(...v)=>h().useEffect(...v);
            export default {createElement:(...v)=>h().createElement(...v)};
        ` }));
    } }] });
const { default: Player } = await import(pathToFileURL(output));
// Media failures in unit tests never contact a real audio host.
mock.method(globalThis, 'fetch', () => Promise.reject(new TypeError('CORS denied')));
const find = (node, predicate) => {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of React.Children.toArray(node.props?.children)) {
        const found = find(child, predicate);
        if (found) return found;
    }
    return null;
};
const byClass = (tree, name) => find(tree, (node) => node.props?.className === name);
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((a, b) => { resolve = a; reject = b; });
    return { promise, resolve, reject };
};
const flush = () => new Promise((resolve) => setImmediate(resolve));
const audioResponse = () => new Response(new Blob(['synthetic ogg'], { type: 'audio/ogg' }));

function copyFixture(t) {
    const pending = deferred(), calls = [], created = [], revoked = [];
    t.mock.method(globalThis, 'fetch', (url, options) => { calls.push({ url, ...options }); return pending.promise; });
    t.mock.method(URL, 'createObjectURL', (blob) => { created.push(blob); return 'blob:audio-copy'; });
    t.mock.method(URL, 'revokeObjectURL', (url) => { revoked.push(url); });
    return { pending, calls, created, revoked };
}

function indefinite(h, position = 10) {
    h.audio.duration = Infinity;
    h.audio.seekable = { length: 1, start: () => 0, end: () => Infinity };
    h.playbackTime(position);
    h.event('onLoadedMetadata');
}

function fixture({ play, realisticLoad = false } = {}) {
    const slots = [];
    let cursor = 0, effects = [], unmounted = false, writesAfterUnmount = 0, handlers;
    let currentTime = 0;
    const audio = {
        paused: true, ended: false, error: null, duration: NaN, currentTime: 0, playbackRate: 1,
        playCalls: 0, pauseCalls: 0, loadCalls: 0, seekCalls: [], seeking: false,
        getAttribute: (name) => audio[name],
        removeAttribute: (name) => { delete audio[name]; },
        load() { this.loadCalls += 1; this.error = null;
            if (realisticLoad) { this.paused = true; this.duration = NaN; currentTime = 0; this.playbackRate = 1; handlers.onPause(); } },
        play() { this.playCalls += 1; this.paused = false; handlers.onPlay();
            return play ? play() : Promise.resolve().then(() => handlers.onPlaying()); },
        pause() { this.pauseCalls += 1;
            if (!this.paused) { this.paused = true; handlers.onPause(); } },
    };
    Object.defineProperty(audio, 'currentTime', {
        get() { return currentTime; },
        set(value) { currentTime = value; audio.seekCalls.push(value); },
    });
    const h = {
        audio, createElement: React.createElement,
        playbackTime(value) { currentTime = value; },
        useState(initial) { const i = cursor++; slots[i] ??= { value: initial };
            return [slots[i].value, (value) => {
                if (unmounted) writesAfterUnmount += 1;
                slots[i].value = typeof value === 'function' ? value(slots[i].value) : value;
            }]; },
        useRef(initial) { const i = cursor++; slots[i] ??= { current: initial }; return slots[i]; },
        useEffect(effect, deps) { const i = cursor++, old = slots[i];
            if (old && deps.every((v, k) => Object.is(v, old.deps[k]))) return;
            slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); }); },
        render() {
            globalThis.__audioHarness = h;
            cursor = 0;
            const element = Player({ src: 'https://example.invalid/voice.ogg' });
            const tree = element.type(element.props);
            const media = find(tree, (node) => node.type === 'audio');
            media.ref.current = audio;
            handlers = media.props;
            const pending = effects; effects = []; pending.forEach((fn) => fn());
            return tree;
        },
        event(name) { handlers[name](); },
        unmount() { if (!unmounted) { unmounted = true; slots.forEach((slot) => slot?.cleanup?.()); } },
        restore() { h.unmount(); delete globalThis.__audioHarness; },
        get lateWrites() { return writesAfterUnmount; },
    };
    return h;
}

test('a thread does not load audio before play, and speed cycles 1 / 1.5 / 2 without downloads', () => {
    const h = fixture();
    try {
        let tree = h.render();
        const media = find(tree, (node) => node.type === 'audio');
        assert.equal(media.props.preload, 'none');
        assert.equal(media.props.src, undefined);
        assert.equal(h.audio.src, undefined);
        assert.equal(byClass(tree, 'wazzup-audio-seek').props.disabled, true);
        for (const rate of [1.5, 2, 1]) {
            byClass(tree, 'wazzup-audio-speed').props.onClick();
            tree = h.render();
            assert.equal(h.audio.playbackRate, rate);
            assert.match(byClass(tree, 'wazzup-audio-speed').props['aria-label'], new RegExp(`Скорость ${rate}×`));
        }
        assert.equal(h.audio.loadCalls, 0);
        assert.equal(h.audio.playCalls, 0);
        assert.equal(h.audio.src, undefined);
    } finally { h.restore(); }
});

test('dragging an already playing message previews locally and seeks once when the pointer is released', async () => {
    const h = fixture();
    try {
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        h.audio.duration = 90;
        h.playbackTime(10);
        h.event('onLoadedMetadata');
        let range = byClass(h.render(), 'wazzup-audio-seek');
        const target = { value: '10', setPointerCapture() {} };
        range.props.onPointerDown?.({ currentTarget: target, pointerId: 7, button: 0 });
        for (const value of [20, 25, 30, 35, 40]) {
            target.value = String(value);
            range.props.onChange({ target });
            h.playbackTime(11); h.event('onTimeUpdate');
            range = byClass(h.render(), 'wazzup-audio-seek');
            assert.equal(range.props.value, value, 'media timeupdate must not pull the thumb back during a drag');
        }
        assert.deepEqual(h.audio.seekCalls, [], 'pointer movement must not repeatedly restart a media seek');
        range.props.onPointerUp({ currentTarget: target, pointerId: 7 });
        assert.deepEqual(h.audio.seekCalls, [40]);
        assert.equal(h.audio.paused, false);
        assert.equal(h.audio.playCalls, 1);
        assert.equal(h.audio.loadCalls, 0);
        range.props.onLostPointerCapture?.({ pointerId: 7 });
        assert.deepEqual(h.audio.seekCalls, [40], 'native capture cleanup must not seek twice');
        h.audio.seeking = true; h.playbackTime(0); h.event('onTimeUpdate');
        assert.equal(byClass(h.render(), 'wazzup-audio-seek').props.value, 40,
            'a decoder seeking through the start must not reset the selected position');
        h.audio.seeking = false; h.playbackTime(40); h.event('onSeeked');
        h.playbackTime(41); h.event('onTimeUpdate');
        assert.equal(byClass(h.render(), 'wazzup-audio-seek').props.value, 41);
    } finally { h.restore(); }
});

test('an OGG stream with unknown duration keeps finite stable bounds during scrubbing', async () => {
    const h = fixture();
    let seekableEnd = 60;
    try {
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        h.audio.duration = Infinity;
        h.audio.seekable = { length: 1, start: () => 0, end: () => seekableEnd };
        h.playbackTime(10); h.event('onLoadedMetadata');
        let range = byClass(h.render(), 'wazzup-audio-seek');
        const target = { value: '10', setPointerCapture() {} };
        assert.equal(range.props.max, 60);
        range.props.onPointerDown?.({ currentTarget: target, pointerId: 1, button: 0 });
        target.value = '40'; range.props.onChange({ target });
        seekableEnd = 80; h.event('onTimeUpdate');
        range = byClass(h.render(), 'wazzup-audio-seek');
        assert.equal(range.props.max, 60, 'new buffering must not change the scale beneath the pointer');
        range.props.onPointerUp({ currentTarget: target, pointerId: 1 });
        assert.deepEqual(h.audio.seekCalls, [40]);
        seekableEnd = Infinity; h.event('onDurationChange');
        assert.ok(Number.isFinite(byClass(h.render(), 'wazzup-audio-seek').props.max));
    } finally { h.restore(); }
});

test('a cancelled pointer drag leaves playback untouched and a paused keyboard seek stays paused', async () => {
    const h = fixture();
    try {
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        h.audio.duration = 90; h.playbackTime(10); h.event('onLoadedMetadata');
        let range = byClass(h.render(), 'wazzup-audio-seek');
        const target = { value: '10', setPointerCapture() {} };
        range.props.onPointerDown({ currentTarget: target, pointerId: 2, button: 0 });
        target.value = '30'; range.props.onChange({ target });
        h.playbackTime(12); h.event('onTimeUpdate');
        range = byClass(h.render(), 'wazzup-audio-seek');
        assert.equal(range.props.value, 30);
        range.props.onPointerCancel({ pointerId: 2 });
        assert.deepEqual(h.audio.seekCalls, []);
        assert.equal(byClass(h.render(), 'wazzup-audio-seek').props.value, 12);
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        range = byClass(h.render(), 'wazzup-audio-seek');
        range.props.onChange({ target: { value: '12.1' } });
        assert.deepEqual(h.audio.seekCalls, [12.1]);
        h.event('onSeeked');
        assert.equal(h.audio.paused, true);
        assert.equal(h.audio.playCalls, 1);
        assert.equal(h.audio.loadCalls, 0);
        assert.equal(byClass(h.render(), 'wazzup-audio-seek').props.value, 12.1);
    } finally { h.restore(); }
});

test('seeking to the current position does not wait forever for a nonexistent seeked event', async () => {
    const h = fixture();
    try {
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        h.audio.duration = 90; h.playbackTime(10); h.event('onLoadedMetadata');
        byClass(h.render(), 'wazzup-audio-seek').props.onChange({ target: { value: '10' } });
        assert.deepEqual(h.audio.seekCalls, []);
        h.playbackTime(11); h.event('onTimeUpdate');
        assert.equal(byClass(h.render(), 'wazzup-audio-seek').props.value, 11);
    } finally { h.restore(); }
});

test('non-range OGG gets one local copy and resumes at the latest position and selected speed', async (t) => {
    const copy = copyFixture(t), h = fixture({ realisticLoad: true });
    try {
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        indefinite(h, 4); h.event('onTimeUpdate');
        assert.equal(copy.calls.length, 1);
        assert.equal(copy.calls[0].credentials, 'omit');
        byClass(h.render(), 'wazzup-audio-speed').props.onClick(); h.render();
        h.playbackTime(7);
        copy.pending.resolve(audioResponse()); await flush();
        assert.equal(h.audio.src, 'blob:audio-copy');
        assert.equal(h.audio.loadCalls, 1);
        assert.equal(h.audio.playCalls, 1, 'wait for finite metadata before restoring playback');
        h.event('onTimeUpdate');
        h.audio.duration = 30; h.render(); h.event('onLoadedMetadata'); await flush();
        assert.equal(h.audio.currentTime, 7);
        assert.equal(h.audio.playbackRate, 1.5);
        assert.equal(h.audio.paused, false);
        assert.equal(h.audio.playCalls, 2);
        assert.equal(byClass(h.render(), 'wazzup-audio-seek').props.disabled, false);
        h.unmount();
        assert.deepEqual(copy.revoked, ['blob:audio-copy']);
    } finally { h.restore(); }
});

test('pausing during copy fetch or metadata replacement never autoplays afterward', async (t) => {
    for (const pauseAfterFetch of [false, true]) {
        const copy = copyFixture(t), h = fixture({ realisticLoad: true });
        try {
            await byClass(h.render(), 'wazzup-audio-play').props.onClick();
            indefinite(h, 8);
            if (pauseAfterFetch) { copy.pending.resolve(audioResponse()); await flush(); }
            await byClass(h.render(), 'wazzup-audio-play').props.onClick();
            if (!pauseAfterFetch) { copy.pending.resolve(audioResponse()); await flush(); }
            h.audio.duration = 30; h.render(); h.event('onLoadedMetadata'); await flush();
            assert.equal(h.audio.paused, true);
            assert.equal(h.audio.currentTime, 8);
            assert.equal(h.audio.playCalls, 1);
            assert.equal(byClass(h.render(), 'wazzup-audio-play').props['aria-label'], 'Воспроизвести аудио');
        } finally { h.restore(); }
    }
});

test('replacing a still-pending original play ignores its AbortError and honors the requested playback', async (t) => {
    const copy = copyFixture(t), originalPlay = deferred();
    let plays = 0;
    const h = fixture({ realisticLoad: true, play: () => ++plays === 1 ? originalPlay.promise : Promise.resolve() });
    try {
        const original = byClass(h.render(), 'wazzup-audio-play').props.onClick();
        indefinite(h, 3);
        copy.pending.resolve(audioResponse()); await flush();
        originalPlay.reject(new DOMException('source replaced', 'AbortError')); await original;
        h.audio.duration = 30; h.render(); h.event('onLoadedMetadata'); await flush();
        assert.equal(h.audio.currentTime, 3);
        assert.equal(h.audio.paused, false);
        assert.equal(h.audio.playCalls, 2);
        assert.equal(byClass(h.render(), 'wazzup-audio-original'), null);
    } finally { h.restore(); }
});

test('another player takes priority while the first player waits for replacement metadata', async (t) => {
    const copy = copyFixture(t), first = fixture({ realisticLoad: true }), second = fixture();
    try {
        await byClass(first.render(), 'wazzup-audio-play').props.onClick(); indefinite(first, 8);
        copy.pending.resolve(audioResponse()); await flush();
        await byClass(second.render(), 'wazzup-audio-play').props.onClick();
        first.audio.duration = 30; first.render(); first.event('onLoadedMetadata'); await flush();
        assert.equal(first.audio.paused, true);
        assert.equal(first.audio.playCalls, 1);
        assert.equal(second.audio.paused, false);
    } finally { first.restore(); second.restore(); }
});

test('unmount aborts copy download and a late response cannot replace media or leak an object URL', async (t) => {
    const copy = copyFixture(t), h = fixture();
    try {
        await byClass(h.render(), 'wazzup-audio-play').props.onClick(); indefinite(h);
        h.unmount();
        assert.equal(copy.calls[0].signal.aborted, true);
        copy.pending.resolve(audioResponse()); await flush();
        assert.deepEqual(copy.created, []);
        assert.equal(h.audio.src, undefined);
        assert.equal(h.lateWrites, 0);
    } finally { h.restore(); }
});

test('CORS failure leaves original audio playing, and finite-duration media never requests a copy', async (t) => {
    const copy = copyFixture(t), h = fixture();
    try {
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        h.audio.duration = 30; h.event('onLoadedMetadata');
        assert.equal(copy.calls.length, 0);
        indefinite(h);
        copy.pending.reject(new TypeError('CORS denied')); await flush();
        h.event('onTimeUpdate');
        assert.equal(copy.calls.length, 1, 'failed copy is not fetched repeatedly');
        assert.equal(h.audio.src, 'https://example.invalid/voice.ogg');
        assert.equal(h.audio.paused, false);
        assert.equal(h.audio.loadCalls, 0);
        assert.deepEqual(copy.created, []);
        assert.equal(byClass(h.render(), 'wazzup-audio-original'), null);
        assert.equal(byClass(h.render(), 'wazzup-audio-seek').props.disabled, true);
    } finally { h.restore(); }
});

test('a chunked copy exceeding the byte cap is cancelled before it can replace the original', async (t) => {
    const copy = copyFixture(t), h = fixture();
    let chunks = 0, cancelled = false;
    try {
        await byClass(h.render(), 'wazzup-audio-play').props.onClick(); indefinite(h);
        copy.pending.resolve(new Response(new ReadableStream({
            pull(controller) { chunks += 1; controller.enqueue(new Uint8Array(17 * 1024 * 1024)); },
            cancel() { cancelled = true; },
        }), { headers: { 'Content-Type': 'audio/ogg' } }));
        await flush();
        assert.equal(cancelled, true);
        assert.ok(chunks <= 3, 'stop reading once the cap is exceeded, even without Content-Length');
        assert.equal(h.audio.loadCalls, 0);
        assert.equal(h.audio.paused, false);
        assert.deepEqual(copy.created, []);
    } finally { h.restore(); }
});

test('play, metadata, keyboard-compatible range seeking, pause and replay use the same media', async () => {
    const h = fixture();
    try {
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        assert.equal(h.audio.src, 'https://example.invalid/voice.ogg');
        assert.equal(byClass(h.render(), 'wazzup-audio-play').props['aria-label'], 'Приостановить аудио');
        h.audio.duration = 95; h.audio.currentTime = 15; h.event('onLoadedMetadata');
        let range = byClass(h.render(), 'wazzup-audio-seek');
        assert.equal(range.props.disabled, false);
        assert.equal(range.props['aria-valuetext'], '0:15 из 1:35');
        range.props.onChange({ target: { value: '42.5' } });
        assert.equal(h.audio.currentTime, 42.5);
        assert.equal(byClass(h.render(), 'wazzup-audio-seek').props.value, 42.5);
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        assert.equal(h.audio.paused, true);
        assert.equal(byClass(h.render(), 'wazzup-audio-play').props['aria-label'], 'Воспроизвести аудио');
        byClass(h.render(), 'wazzup-audio-speed').props.onClick();
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        assert.equal(h.audio.playbackRate, 1.5);
        h.audio.currentTime = 95; h.audio.ended = true; h.audio.paused = true; h.event('onEnded');
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        assert.equal(h.audio.currentTime, 0);
    } finally { h.restore(); }
});

test('a user can pause loading and a late play rejection cannot turn the pause into an error', async () => {
    const pending = deferred(), h = fixture({ play: () => pending.promise });
    try {
        const work = byClass(h.render(), 'wazzup-audio-play').props.onClick();
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        pending.reject(new Error('late network rejection')); await work;
        assert.equal(h.audio.paused, true);
        assert.equal(byClass(h.render(), 'wazzup-audio-play').props['aria-label'], 'Воспроизвести аудио');
        assert.equal(byClass(h.render(), 'wazzup-audio-original'), null);
    } finally { h.restore(); }
});

test('failed audio offers a retry and original link; a retry reloads and starts playback', async () => {
    let attempts = 0;
    const h = fixture({ play: () => ++attempts === 1 ? Promise.reject(new Error('network')) : Promise.resolve() });
    try {
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        h.audio.paused = true; // Native media pauses when a resource fails.
        assert.equal(byClass(h.render(), 'wazzup-audio-play').props['aria-label'], 'Повторить загрузку аудио');
        const link = byClass(h.render(), 'wazzup-audio-original');
        assert.equal(link.props.href, 'https://example.invalid/voice.ogg');
        assert.equal(link.props.rel, 'noopener noreferrer');
        await byClass(h.render(), 'wazzup-audio-play').props.onClick();
        h.event('onPlaying');
        assert.equal(h.audio.loadCalls, 1);
        assert.equal(h.audio.playCalls, 2);
        assert.equal(byClass(h.render(), 'wazzup-audio-original'), null);
    } finally { h.restore(); }
});

test('starting another message pauses the first player', async () => {
    const first = fixture(), second = fixture();
    try {
        await byClass(first.render(), 'wazzup-audio-play').props.onClick();
        await byClass(second.render(), 'wazzup-audio-play').props.onClick();
        assert.equal(first.audio.paused, true);
        assert.equal(second.audio.paused, false);
        assert.equal(byClass(first.render(), 'wazzup-audio-play').props['aria-label'], 'Воспроизвести аудио');
    } finally { first.restore(); second.restore(); }
});

test('source changes remount the player; unmount pauses, releases the source and ignores pending work', async () => {
    const pending = deferred(), h = fixture({ play: () => pending.promise });
    try {
        const work = byClass(h.render(), 'wazzup-audio-play').props.onClick();
        assert.notEqual(Player({ src: 'a.ogg' }).key, Player({ src: 'b.ogg' }).key);
        h.unmount();
        assert.equal(h.audio.paused, true);
        assert.equal(h.audio.src, undefined);
        assert.equal(h.audio.loadCalls, 1);
        pending.reject(new Error('download ended after unmount')); await work;
        assert.equal(h.lateWrites, 0);
    } finally { h.restore(); }
});
