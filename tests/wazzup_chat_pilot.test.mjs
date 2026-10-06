import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import {
    classifyPilotSendFailure, GLOBAL_CHANNEL_IDS, mergePilotMessages,
    pilotChatKey, pilotDraftStorageKey, splitPilotEvents,
} from '../src/components/wazzup/chatPilot.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { build, buildSync } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'ChatPilotComposer.mjs');
buildSync({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/ChatPilotComposer.jsx')],
    outfile: output, bundle: true, format: 'esm', platform: 'node', target: 'node18',
    external: ['react', 'react-dom', 'axios', 'lucide-react'],
});
const { default: ChatPilotComposer } = await import(pathToFileURL(output));
const chat = { channelId: 'test-channel', chatId: '77770000000', chatType: 'whatsapp' };
const render = () => renderToStaticMarkup(React.createElement(ChatPilotComposer, {
    apiBaseUrl: 'https://example.invalid', headers: () => ({}), chat,
}));

test('webhook merge keeps loaded history, applies edits, deduplicates echo and never regresses read', () => {
    const result = mergePilotMessages([
        { messageId: 'old', dt: '2026-10-01T00:00:00Z', text: 'old page' },
        { messageId: 'sent', dt: '2026-10-06T00:00:00Z', text: 'hello', status: 'read' },
    ], [
        { messageId: 'new', dt: '2026-10-06T00:00:01Z', text: 'answer' },
        { messageId: 'sent', dt: '2026-10-06T00:00:00Z', text: 'edited', status: 'sent', isEdited: true },
        { messageId: 'new', dt: '2026-10-06T00:00:01Z', text: 'answer', status: 'delivered' },
    ]);
    assert.deepEqual(result.map((message) => message.messageId), ['old', 'sent', 'new']);
    assert.equal(result[1].status, 'read');
    assert.equal(result[1].text, 'edited');
    assert.equal(result[1].isEdited, true);
    assert.equal(result[2].status, 'delivered');
});

test('SSE parser retains incomplete frames and handles CRLF, multiline JSON and heartbeats', () => {
    const first = splitPilotEvents('event: connected\r\ndata: {}\r\n\r\n: heartbeat\n\nevent: change\nid: 42\nda');
    assert.deepEqual(first.frames, [{ event: 'connected', id: null, data: '{}' }]);
    const second = splitPilotEvents(first.buffer + 'ta: {"messageId":"м",\ndata: "status":"read"}\n\n');
    assert.equal(second.frames[0].event, 'change');
    assert.equal(second.frames[0].id, '42');
    assert.deepEqual(JSON.parse(second.frames[0].data), { messageId: 'м', status: 'read' });
    assert.equal(second.buffer, '');
});

test('a failure replaces pending/sent, but cannot regress delivered/read', () => {
    for (const status of ['pending', 'sent', 'delivered', 'read']) {
        const [result] = mergePilotMessages([{ messageId: 'm1', status }], [{ messageId: 'm1', status: 'error' }]);
        assert.equal(result.status, ['delivered', 'read'].includes(status) ? status : 'error');
    }
});

test('timeouts and ambiguous server errors preserve the send identity', () => {
    for (const error of [{}, { code: 'ECONNABORTED' }, { response: { status: 502 } },
        { response: { status: 409, data: { state: 'unknown', error: 'Pending' } } }]) {
        assert.equal(classifyPilotSendFailure(error).state, 'unknown');
    }
    assert.deepEqual(classifyPilotSendFailure({ response: {
        status: 422, data: { state: 'failed', error: 'Окно WABA закрыто' },
    } }), { state: 'failed', message: 'Окно WABA закрыто' });
    assert.equal(classifyPilotSendFailure({ response: { status: 403 } }).state, 'failed');
});

test('chat identity separates account, channel and opaque chat identifiers', () => {
    assert.notEqual(pilotChatKey('op', chat), pilotChatKey('potok', chat));
    assert.notEqual(pilotChatKey('op', chat), pilotChatKey('op', { ...chat, channelId: 'other' }));
    assert.equal(GLOBAL_CHANNEL_IDS.size, 2);
});

test('empty composer has disabled send and does not perform requests on render', () => {
    const html = render();
    assert.match(html, /Ответ в WhatsApp/);
    assert.match(html, /<button[^>]+disabled=""/);
    assert.doesNotMatch(html, /readOnly=""/);
});

test('returning to a chat restores uncertain attempt with readonly text and check-only action', () => {
    const pending = { account: 'op', ...chat, text: 'Не потерять сообщение', clientMessageId: 'fixed-uuid' };
    const storage = new Map([[pilotDraftStorageKey(chat), JSON.stringify({ text: pending.text, pending })]]);
    const previous = globalThis.sessionStorage;
    globalThis.sessionStorage = { getItem: (key) => storage.get(key) || null };
    try {
        const html = render();
        assert.match(html, /readonly=""/i);
        assert.match(html, /Не потерять сообщение/);
        assert.match(html, /Проверить отправку/);
        assert.doesNotMatch(html, /<button type="submit"[^>]+disabled=""/);
        assert.match(html, /Я проверил результат в переписке/);
        assert.match(html, /<button type="button"[^>]+disabled=""/);
        assert.equal(JSON.parse(storage.get(pilotDraftStorageKey(chat))).pending.clientMessageId, 'fixed-uuid');
    } finally {
        if (previous === undefined) delete globalThis.sessionStorage;
        else globalThis.sessionStorage = previous;
    }
});

// Exercise the real component event handlers with deterministic hook storage.
// No browser, network or extra test renderer is needed for the send state machine.
const interactiveOutput = join(cache, 'ChatPilotComposerInteractive.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/ChatPilotComposer.jsx')],
    outfile: interactiveOutput, bundle: true, format: 'esm', platform: 'node', target: 'node18',
    external: ['lucide-react'],
    plugins: [{ name: 'pilot-state-harness', setup(build) {
        build.onResolve({ filter: /^(react|axios)$/ }, ({ path }) => ({ path, namespace: 'pilot-harness' }));
        build.onLoad({ filter: /.*/, namespace: 'pilot-harness' }, ({ path }) => ({ contents: path === 'react'
            ? `const h=()=>globalThis.__wazzupPilotHarness;
               export const useState=(...args)=>h().useState(...args);
               export const useRef=(...args)=>h().useRef(...args);
               export const useEffect=(...args)=>h().useEffect(...args);
               export default {createElement:(...args)=>h().createElement(...args)};`
            : 'export default {post:(...args)=>globalThis.__wazzupPilotHarness.post(...args)};',
            loader: 'js',
        }));
    } }],
});
const { default: InteractiveComposer } = await import(pathToFileURL(interactiveOutput));

const createHarness = (post) => {
    const slots = [];
    const storage = new Map();
    let index = 0;
    let effects = [];
    const harness = {
        createElement: React.createElement, post,
        useState(initial) {
            const i = index++;
            slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
            return [slots[i].value, (value) => {
                slots[i].value = typeof value === 'function' ? value(slots[i].value) : value;
            }];
        },
        useRef(initial) {
            const i = index++;
            slots[i] ??= { current: initial };
            return slots[i];
        },
        useEffect(effect, dependencies) {
            const i = index++;
            const old = slots[i];
            if (old && dependencies?.every((value, k) => Object.is(value, old.dependencies?.[k]))) return;
            slots[i] = { dependencies };
            effects.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); });
        },
        render() {
            index = 0;
            const wrapper = InteractiveComposer({ apiBaseUrl: '/test-only', headers: () => ({}), chat });
            const result = wrapper.type(wrapper.props);
            const pendingEffects = effects;
            effects = [];
            pendingEffects.forEach((effect) => effect());
            return result;
        },
        storage,
    };
    globalThis.__wazzupPilotHarness = harness;
    globalThis.sessionStorage = {
        getItem: (key) => storage.get(key) || null,
        setItem: (key, value) => storage.set(key, value),
        removeItem: (key) => storage.delete(key),
    };
    return harness;
};

const findElement = (root, predicate) => {
    if (!root || typeof root !== 'object') return null;
    if (predicate(root)) return root;
    for (const child of React.Children.toArray(root.props?.children)) {
        const match = findElement(child, predicate);
        if (match) return match;
    }
    return null;
};

test('double click sends once; uncertain retry uses exactly the original payload', async () => {
    const previousStorage = globalThis.sessionStorage;
    const attempts = [];
    let rejectSend;
    const harness = createHarness((url, body) => {
        attempts.push({ url, body });
        return new Promise((resolve, reject) => { rejectSend = reject; });
    });
    try {
        let form = harness.render();
        findElement(form, (el) => el.type === 'textarea').props.onChange({ target: { value: 'Привет' } });
        form = harness.render();
        const first = form.props.onSubmit();
        await form.props.onSubmit();
        assert.equal(attempts.length, 1);
        rejectSend({ code: 'ECONNABORTED' });
        await first;
        form = harness.render();
        assert.equal(findElement(form, (el) => el.type === 'textarea').props.readOnly, true);
        const retry = form.props.onSubmit();
        assert.equal(attempts.length, 2);
        assert.deepEqual(attempts[1].body, attempts[0].body);
        rejectSend({ response: { status: 409, data: { state: 'unknown' } } });
        await retry;
        form = harness.render();
        const expiredLoginCheck = form.props.onSubmit();
        rejectSend({ response: { status: 403 } });
        await expiredLoginCheck;
        form = harness.render();
        assert.equal(findElement(form, (el) => el.type === 'textarea').props.readOnly, true);
        assert.deepEqual(attempts[2].body, attempts[0].body);
    } finally {
        delete globalThis.__wazzupPilotHarness;
        if (previousStorage === undefined) delete globalThis.sessionStorage;
        else globalThis.sessionStorage = previousStorage;
    }
});

test('only explicit conversation check clears uncertain send; reset does not send a message', async () => {
    const previousStorage = globalThis.sessionStorage;
    let sends = 0;
    const harness = createHarness(async () => { sends += 1; throw { code: 'ECONNABORTED' }; });
    try {
        let form = harness.render();
        findElement(form, (el) => el.type === 'textarea').props.onChange({ target: { value: 'Проверить вручную' } });
        form = harness.render();
        await form.props.onSubmit();
        form = harness.render();
        let reset = findElement(form, (el) => el.type === 'button' && el.props.type === 'button');
        assert.equal(reset.props.disabled, true);
        reset.props.onClick();
        assert.equal(findElement(harness.render(), (el) => el.type === 'textarea').props.readOnly, true);
        findElement(form, (el) => el.type === 'input' && el.props.type === 'checkbox')
            .props.onChange({ target: { checked: true } });
        form = harness.render();
        reset = findElement(form, (el) => el.type === 'button' && el.props.type === 'button');
        assert.equal(reset.props.disabled, false);
        reset.props.onClick();
        form = harness.render();
        const field = findElement(form, (el) => el.type === 'textarea');
        assert.equal(field.props.value, '');
        assert.equal(field.props.readOnly, false);
        assert.equal(sends, 1);
        assert.equal(harness.storage.size, 0);
    } finally {
        delete globalThis.__wazzupPilotHarness;
        if (previousStorage === undefined) delete globalThis.sessionStorage;
        else globalThis.sessionStorage = previousStorage;
    }
});
