import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { build } from 'esbuild';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzup-video-message.mjs');
await build({ entryPoints: ['src/components/wazzup/ChatMessageVideo.jsx'], outfile: output,
    bundle: true, format: 'esm', platform: 'node', external: ['lucide-react'],
    plugins: [{ name: 'video-hooks', setup(builder) {
        builder.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'fixture' }));
        builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: `
            const h=()=>globalThis.__videoHarness;
            export const useRef=(...v)=>h().useRef(...v);
            export const useEffect=(...v)=>h().useEffect(...v);
            export default {createElement:(...v)=>h().createElement(...v)};
        ` }));
    } }] });
const { default: VideoMessage } = await import(pathToFileURL(output));

test('video bubble opens one modal and never plays a second inline player; cleanup releases metadata', () => {
    const src = 'https://example.invalid/synthetic.mp4';
    let setup, closed, opened = 0;
    const media = { src, pauses: 0, loads: 0, pause() { this.pauses++; }, load() { this.loads++; },
        removeAttribute(name) { delete this[name]; }, getAttribute(name) { return this[name]; } };
    const ref = { current: media };
    globalThis.__videoHarness = { createElement: React.createElement, useRef: () => ref,
        useEffect: (fn) => { setup = fn; } };
    try {
        const tree = VideoMessage({ src, label: 'Видео', onOpen: () => { opened++; } });
        const preview = React.Children.toArray(tree.props.children).find((node) => node.type === 'video');
        assert.equal(preview.props.controls, undefined);
        assert.equal(preview.props.autoPlay, undefined);
        assert.equal(preview.props.muted, true);
        assert.equal(preview.props.tabIndex, -1);
        assert.equal(preview.props.preload, 'metadata');
        tree.props.onClick(); assert.equal(opened, 1);
        closed = setup(); closed();
        assert.equal(media.pauses, 1); assert.equal(media.loads, 1); assert.equal(media.src, undefined);
        // StrictMode effect replay keeps the preview available after cleanup.
        closed = setup(); assert.equal(media.src, src); closed();
        assert.equal(media.src, undefined);
    } finally { delete globalThis.__videoHarness; }
});
