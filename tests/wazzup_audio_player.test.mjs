import test from 'node:test';
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

function fixture({ play } = {}) {
    const slots = [];
    let cursor = 0, effects = [], unmounted = false, writesAfterUnmount = 0, handlers;
    const audio = {
        paused: true, ended: false, error: null, duration: NaN, currentTime: 0, playbackRate: 1,
        playCalls: 0, pauseCalls: 0, loadCalls: 0,
        getAttribute: (name) => audio[name],
        removeAttribute: (name) => { delete audio[name]; },
        load() { this.loadCalls += 1; this.error = null; },
        play() { this.playCalls += 1; this.paused = false; handlers.onPlay();
            return play ? play() : Promise.resolve().then(() => handlers.onPlaying()); },
        pause() { this.pauseCalls += 1;
            if (!this.paused) { this.paused = true; handlers.onPause(); } },
    };
    const h = {
        audio, createElement: React.createElement,
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
