import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const view = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8');
const bubbleSource = view.slice(view.indexOf('const MessageBubble ='), view.indexOf('// Own state keeps scrolling'));
const outfile = join(cache, 'wazzup-message-bubble.mjs');
await build({ stdin: { contents: `
    import React from 'react';
    import { Headset, Ban, Clock3, Reply } from 'lucide-react';
    import MessageDeliveryStatus from './MessageDeliveryStatus';
    import ChatMessageText from './ChatMessageText';
    import { canReplyOnDoubleClick } from './threadPresentation';
    const MEDIA_LABELS={image:'Фото'};
    const MediaContent=()=>null;
    const lateDeliveryNote=()=>null;
    const fmtTime=()=> '12:34';
    ${bubbleSource}
    export default MessageBubble;
`, resolveDir: join(process.cwd(), 'src/components/wazzup'), loader: 'jsx' },
    outfile, bundle: true, platform: 'node', format: 'esm', external: ['react', 'lucide-react'] });
const { default: Bubble } = await import(pathToFileURL(outfile));
const find = (node, predicate) => {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of React.Children.toArray(node.props?.children)) {
        const match = find(child, predicate); if (match) return match;
    }
    return null;
};
const msg = { messageId: 'synthetic', dt: '2026-10-08T12:34:00Z', type: 'text', text: 'Тест' };

test('outgoing delivery uses gray/blue double checks, accessible labels and no visible status text or dot', () => {
    for (const [status, label, color] of [['delivered', 'Доставлено', 'text-slate-500'], ['read', 'Прочитано', 'text-sky-500']]) {
        const markup = renderToStaticMarkup(React.createElement(Bubble, { msg: { ...msg, isEcho: true, status } }));
        assert.match(markup, /bg-\[#dcf8c6\]/);
        assert.match(markup, /lucide-check-check/);
        assert.ok(markup.includes(`aria-label="${label}"`) && markup.includes(color));
        assert.doesNotMatch(markup, new RegExp(`>${label}<`));
        assert.doesNotMatch(markup, />·|· /);
    }
    const incoming = renderToStaticMarkup(React.createElement(Bubble, { msg: { ...msg, status: 'read' } }));
    assert.doesNotMatch(incoming, /lucide-check-check/);
});

test('a message still in the queue or just accepted by Wazzup shows one gray check, not a clock', () => {
    for (const [status, label] of [['queued', 'Отправляется'], ['pending', 'Принято Wazzup'], ['sent', 'Отправлено']]) {
        const markup = renderToStaticMarkup(React.createElement(Bubble, { msg: { ...msg, isEcho: true, status } }));
        assert.match(markup, /lucide-check(?!-)/, status);
        assert.doesNotMatch(markup, /lucide-clock|lucide-check-check/, status);
        assert.ok(markup.includes(`aria-label="${label}"`) && markup.includes('text-slate-500'), status);
    }
});

test('double click on a message chooses the reply without activating embedded media or deleted messages', () => {
    const replies = [];
    const tree = Bubble.type({ msg, onReply: (item) => replies.push(item) });
    const body = find(tree, (node) => node.props?.onDoubleClick);
    body.props.onDoubleClick({ target: { closest: () => null }, preventDefault() {} });
    assert.deepEqual(replies, [msg]);
    body.props.onDoubleClick({ target: { closest: () => ({ tagName: 'BUTTON' }) }, preventDefault() {} });
    assert.equal(replies.length, 1);
    const deleted = Bubble.type({ msg: { ...msg, isDeleted: true }, onReply: (item) => replies.push(item) });
    find(deleted, (node) => node.props?.onDoubleClick).props.onDoubleClick({ target: {}, preventDefault() {} });
    assert.equal(replies.length, 1);
});

test('continuing an operator group hides only the author label and aligns the reply button with the bubble', () => {
    const outgoing = { ...msg, isEcho: true, authorName: 'Ядигаров Руслан', status: 'read' };
    const first = renderToStaticMarkup(React.createElement(Bubble, { msg: outgoing, onReply() {} }));
    const continuation = renderToStaticMarkup(React.createElement(Bubble, { msg: outgoing, showAuthor: false, onReply() {} }));
    assert.match(first, /Ядигаров Руслан/);
    assert.doesNotMatch(continuation, /Ядигаров Руслан|mt-6/);
    assert.match(continuation, /Тест/);
    assert.match(continuation, /lucide-check-check/);
    assert.match(continuation, /Ответить на сообщение/);
});

const buttonsOf = (node, found = []) => {
    if (!node || typeof node !== 'object') return found;
    if (node.type === 'button') found.push(node);
    React.Children.toArray(node.props?.children).forEach((child) => buttonsOf(child, found));
    return found;
};
const label = (button) => React.Children.toArray(button.props.children).join('');

test('a failed or unconfirmed own message offers one fix and one way out under the bubble, wired to the right action', () => {
    const local = (state, editable) => ({ ...msg, messageId: 'local:x', clientMessageId: 'x', isEcho: true,
        status: state, local: { state, error: 'Причина от сервера', pinned: false, editable } });
    for (const [state, editable, expected] of [
        ['failed', true, { Повторить: 'retry', Изменить: 'edit' }],
        ['failed', false, { Повторить: 'retry', Убрать: 'discard' }],
        ['unknown', false, { Проверить: 'retry', Убрать: 'discard' }]]) {
        const calls = [];
        const tree = Bubble.type({ msg: local(state, editable), onRetry: (id) => calls.push(['retry', id]),
            onEdit: (id) => calls.push(['edit', id]), onDiscard: (id) => calls.push(['discard', id]) });
        const row = find(tree, (node) => node.props?.['data-testid'] === 'wazzup-send-problem');
        assert.ok(row, `${state}: a problem row`);
        assert.equal(row.props.role, 'alert');
        const buttons = buttonsOf(row);
        assert.deepEqual(buttons.map(label), Object.keys(expected));
        buttons.forEach((button) => button.props.onClick());
        assert.deepEqual(calls, Object.values(expected).map((action) => [action, 'x']));
        assert.match(renderToStaticMarkup(React.createElement(Bubble, { msg: local(state, editable) })), /Причина от сервера/);
    }
    for (const state of ['queued', 'sending', 'sent']) {
        const tree = Bubble.type({ msg: { ...local(state, false), status: 'queued' } });
        assert.equal(find(tree, (node) => node.props?.['data-testid'] === 'wazzup-send-problem'), null, `${state}: no row`);
    }
    const queued = renderToStaticMarkup(React.createElement(Bubble, { msg: { ...local('sending', false), status: 'queued' } }));
    assert.match(queued, /aria-label="Отправляется"/);
});

const imageOutput = join(cache, 'wazzup-stable-image.mjs');
await build({ entryPoints: ['src/components/wazzup/ChatMessageImage.jsx'], outfile: imageOutput,
    bundle: true, platform: 'node', format: 'esm', external: ['lucide-react'],
    plugins: [{ name: 'image-state', setup(builder) {
        builder.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'image-state' }));
        builder.onLoad({ filter: /.*/, namespace: 'image-state' }, () => ({ contents:
            `export const useState=()=>[globalThis.__imageStatus,(value)=>globalThis.__imageStatus=value];
             export default {createElement:(...args)=>globalThis.__imageReact.createElement(...args)};` }));
    } }] });
const { default: ImagePreview } = await import(pathToFileURL(imageOutput));

test('image load and failure retain the same reserved box and do not use natural-size flow', () => {
    globalThis.__imageReact = React;
    globalThis.__imageStatus = 'loading';
    try {
        const entry = ImagePreview({ src: 'https://example.invalid/synthetic.png', onOpen() {} });
        const render = () => entry.type(entry.props);
        const initial = render();
        const image = find(initial, (node) => node.type === 'img');
        assert.match(initial.props.className, /aspect-\[4\/3\]/);
        assert.match(image.props.className, /absolute/);
        image.props.onLoad();
        assert.equal(render().props.className, initial.props.className);
        image.props.onError();
        assert.equal(render().props.className, initial.props.className);
        assert.notEqual(ImagePreview({ src: 'new.png' }).key, entry.key);
    } finally { delete globalThis.__imageReact; delete globalThis.__imageStatus; }
});
