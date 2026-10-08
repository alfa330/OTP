import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { tokenizeMessageEmoji, messageEmojiImageCode } from '../src/components/wazzup/chatMessageEmoji.js';
import { APPLE_EMOJI_BASE, appleEmojiUrl, handleEmojiImageError } from '../src/components/wazzup/chatEmojiImages.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const outfile = join(cache, 'wazzup-message-emoji.mjs');
const compiled = await build({ entryPoints: ['src/components/wazzup/ChatMessageText.jsx'], outfile,
    bundle: true, platform: 'node', format: 'esm', external: ['react'], metafile: true });
const { default: MessageText } = await import(pathToFileURL(outfile));

test('mixed text preserves exact whitespace and original Unicode while mapping Apple emoji sequences', () => {
    const original = 'Сәлем!  ☺ 🤝\nСпасибо 👩🏽‍💻 🇰🇿 1️⃣ 🏳️‍🌈 ❤️';
    const tokens = tokenizeMessageEmoji(original);
    assert.equal(tokens.map((token) => token.text).join(''), original);
    assert.deepEqual(tokens.filter((token) => token.unified).map((token) => token.unified), [
        '263a-fe0f', '1f91d', '1f469-1f3fd-200d-1f4bb', '1f1f0-1f1ff',
        '0031-fe0f-20e3', '1f3f3-fe0f-200d-1f308', '2764-fe0f',
    ]);
    assert.equal(tokenizeMessageEmoji(original), tokens, 'unchanged messages reuse bounded token cache');
});

test('qualified, unqualified and complex emoji use complete image sequences rather than separate glyphs', () => {
    for (const [text, unified] of [
        ['☺', '263a-fe0f'], ['☺️', '263a-fe0f'], ['👍🏾', '1f44d-1f3fe'],
        ['👨‍👩‍👧‍👦', '1f468-200d-1f469-200d-1f467-200d-1f466'],
        ['👩‍⚕', '1f469-200d-2695-fe0f'], ['1⃣', '0031-fe0f-20e3'],
        ['🫱🏽‍🫲🏻', '1faf1-1f3fd-200d-1faf2-1f3fb'],
        ['🏴\u{e0067}\u{e0062}\u{e0065}\u{e006e}\u{e0067}\u{e007f}', '1f3f4-e0067-e0062-e0065-e006e-e0067-e007f'],
    ]) {
        assert.deepEqual(tokenizeMessageEmoji(text), [{ text, unified }]);
    }
});

test('text symbols, explicit text presentation and unsupported sequences remain original text', () => {
    const plain = 'Счёт №123 #456 * © 2026 ® ™ ☺︎';
    assert.deepEqual(tokenizeMessageEmoji(plain), [{ text: plain }]);
    const unknown = '😀‍🐈';
    assert.deepEqual(tokenizeMessageEmoji(unknown), [{ text: unknown }]);
    assert.equal(messageEmojiImageCode('©'), null);
    assert.equal(messageEmojiImageCode('©️'), '00a9-fe0f');
    assert.deepEqual(tokenizeMessageEmoji(''), []);
    assert.deepEqual(tokenizeMessageEmoji(null), []);
});

test('rendering keeps selectable Unicode text, stable inline dimensions and decorative lazy images', () => {
    const original = 'Спасибо ☺\nДа 👍🏾';
    const tree = MessageText.type({ text: original, className: 'whitespace-pre-wrap' });
    const textContent = (node) => {
        if (typeof node === 'string' || typeof node === 'number') return String(node);
        if (Array.isArray(node)) return node.map(textContent).join('');
        return node?.props?.children == null ? '' : textContent(node.props.children);
    };
    assert.equal(textContent(tree), original, 'ordinary DOM text selection retains the exact original emoji');
    const images = tree.props.children.filter((node) => typeof node !== 'string');
    for (const container of images) {
        assert.equal(container.props.style.width, '1.25em');
        assert.equal(container.props.style.height, '1.25em');
        assert.equal(container.props.style.whiteSpace, 'nowrap');
        assert.equal(container.props.style.overflow, 'hidden');
        assert.equal(container.props.children[0].props.style.opacity, 0,
            'colored system glyphs cannot show through before the Apple artwork loads');
        const image = container.props.children[1];
        assert.equal(image.props.alt, '');
        assert.equal(image.props['aria-hidden'], 'true');
        assert.equal(image.props.draggable, false);
        assert.equal(image.props.loading, 'lazy');
        assert.equal(image.props.decoding, 'async');
        assert.equal(image.props.style.userSelect, 'none');
        assert.equal(image.props.style.position, 'absolute');
    }
    const markup = renderToStaticMarkup(React.createElement(MessageText, { text: '<script> ☺' }));
    assert.match(markup, /&lt;script&gt;/);
    assert.match(markup, /263a-fe0f\.png/);
    assert.doesNotMatch(markup, /<script>/);
    assert.ok(Object.keys(compiled.metafile.inputs).every((input) => !input.includes('emoji-picker-react')),
        'rendering chat text never imports the emoji picker or its translated dictionary');
});

test('an unavailable Apple image falls back once and the failure cache avoids repeated CDN requests', () => {
    const unified = '1f44b-1f3ff';
    const img = { tagName: 'IMG', src: appleEmojiUrl(unified) };
    assert.equal(img.src, `${APPLE_EMOJI_BASE}${unified}.png`);
    let stopped = 0;
    const event = { target: img, stopPropagation() { stopped += 1; } };
    handleEmojiImageError(event);
    assert.match(img.src, /^data:image\/svg\+xml,/);
    assert.equal(appleEmojiUrl(unified), img.src);
    handleEmojiImageError(event);
    assert.equal(stopped, 1, 'fallback failures cannot form a network or render loop');
    assert.match(decodeURIComponent(img.src), /👋🏿/);
});
