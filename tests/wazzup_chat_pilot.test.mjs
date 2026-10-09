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
import { prepareTemplate } from '../src/components/wazzup/chatTemplates.js';
import { MAX_UPLOAD_BYTES, MAX_UPLOAD_IMAGE_BYTES, uploadedAttachment, uploadFileError } from '../src/components/wazzup/chatUploads.js';
import { File } from 'node:buffer';

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
const render = (props = {}) => renderToStaticMarkup(React.createElement(ChatPilotComposer, {
    apiBaseUrl: 'https://example.invalid', headers: () => ({}), chat, ...props,
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
    assert.doesNotMatch(html, /Ответ в WhatsApp/);
    assert.match(html, /rows="1"/);
    assert.match(html, /Shift\+Enter/);
    assert.match(html, /<button[^>]+disabled=""/);
    assert.doesNotMatch(html, /readOnly=""/);
});

test('WABA composer shows the template hint only when the known last incoming message is at least 24 hours old', () => {
    const lastInboundAt = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString();
    const html = render({ channelTransport: 'wapi', lastInboundAt });
    assert.match(html, /24 часов после последнего сообщения клиента/);
    assert.match(html, /Чтобы написать первым или после 24 часов/);
    assert.match(html, /одобренный шаблон WABA из Wazzup/);
    assert.match(html, /aria-describedby="wazzup-pilot-message-help wazzup-waba-window-help"/);
    assert.doesNotMatch(render({ channelTransport: 'whatsapp', lastInboundAt }), /wazzup-waba-window-help/);
    assert.doesNotMatch(render({ channelTransport: 'wapi' }), /wazzup-waba-window-help/);
    assert.doesNotMatch(render({ channelTransport: 'wapi', lastInboundAt: new Date().toISOString() }), /wazzup-waba-window-help/);
});

test('a legacy uncertain draft is not offered for a second send: its text is left to the send queue', () => {
    const pending = { account: 'op', ...chat, text: 'Не потерять сообщение', clientMessageId: 'fixed-uuid' };
    const storage = new Map([[pilotDraftStorageKey(chat), JSON.stringify({ text: pending.text, pending })]]);
    const previous = globalThis.sessionStorage;
    globalThis.sessionStorage = { getItem: (key) => storage.get(key) || null };
    try {
        const html = render();
        assert.doesNotMatch(html, /Не потерять сообщение/);
        assert.doesNotMatch(html, /readonly=""/i);
        assert.doesNotMatch(html, /Проверить отправку|Я проверил результат/);
        // The composer never rewrites the stored attempt: sendQueue.js adopts it with its own id.
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
               export const memo=(component)=>component;
               export const lazy=()=>function LazyFixture(){return null;};
               export const Suspense=({children})=>children;
               export default {createElement:(...args)=>h().createElement(...args)};`
            : 'export default {post:(...args)=>globalThis.__wazzupPilotHarness.post(...args)};',
            loader: 'js',
        }));
    } }],
});
const { default: InteractiveComposer } = await import(pathToFileURL(interactiveOutput));

const createHarness = (post, { storage = new Map(), props = {} } = {}) => {
    const slots = [];
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
        render(nextProps) {
            if (nextProps) Object.assign(props, nextProps);
            index = 0;
            const wrapper = InteractiveComposer({ apiBaseUrl: '/test-only', headers: () => ({}), chat, ...props });
            const result = wrapper.type(wrapper.props);
            const pendingEffects = effects;
            effects = [];
            pendingEffects.forEach((effect) => effect());
            return result;
        },
        unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
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

test('double submit queues one message and frees the field at once, before any server answer', () => {
    const previousStorage = globalThis.sessionStorage;
    const queued = [];
    const harness = createHarness(() => { throw new Error('The composer itself must not send'); },
        { props: { onSend: (message) => queued.push(message), authorName: 'Оператор' } });
    try {
        let form = harness.render();
        findElement(form, (el) => el.type === 'textarea').props.onChange({ target: { value: '  Привет  ' } });
        form = harness.render();
        form.props.onSubmit();
        form.props.onSubmit();
        assert.equal(queued.length, 1);
        assert.match(queued[0].clientMessageId, /^[0-9a-f-]{36}$/);
        assert.deepEqual({ ...queued[0], clientMessageId: 'id' }, { clientMessageId: 'id', account: 'op',
            channelId: chat.channelId, chatId: chat.chatId, text: 'Привет', displayText: 'Привет', authorName: 'Оператор' });
        const field = findElement(harness.render(), (el) => el.type === 'textarea');
        assert.equal(field.props.value, '');
        assert.equal(field.props.readOnly, false);
        assert.equal(harness.storage.size, 0);
        findElement(harness.render(), (el) => el.type === 'textarea').props.onChange({ target: { value: 'Второе' } });
        harness.render().props.onSubmit();
        assert.equal(queued.length, 2);
        assert.notEqual(queued[1].clientMessageId, queued[0].clientMessageId);
    } finally {
        delete globalThis.__wazzupPilotHarness;
        if (previousStorage === undefined) delete globalThis.sessionStorage;
        else globalThis.sessionStorage = previousStorage;
    }
});

test('a queue that refuses the message leaves the text in the field and says so', () => {
    const previousStorage = globalThis.sessionStorage;
    const h = createHarness(() => { throw new Error('The composer itself must not send'); },
        { props: { onSend: () => null } });
    try {
        findElement(h.render(), (el) => el.type === 'textarea').props.onChange({ target: { value: 'Не потерять' } });
        h.render().props.onSubmit();
        const form = h.render();
        assert.equal(findElement(form, (el) => el.type === 'textarea').props.value, 'Не потерять');
        assert.ok(findElement(form, (el) => el.props?.role === 'alert'));
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).text, 'Не потерять');
    } finally {
        delete globalThis.__wazzupPilotHarness;
        if (previousStorage === undefined) delete globalThis.sessionStorage;
        else globalThis.sessionStorage = previousStorage;
    }
});

test('approved template is shown as text but queued as its Wazzup code', () => {
    const previousStorage = globalThis.sessionStorage;
    const queued = [];
    const template = { source: 'wazzup', supported: true, templateCode: '[[welcome]][[bodyVar1]]',
        text: 'Здравствуйте, {{1}}!', variables: ['bodyVar1'] };
    const prepared = prepareTemplate(template, { bodyVar1: 'Алия' });
    const h = createHarness(() => { throw new Error('The composer itself must not send'); },
        { props: { onSend: (message) => queued.push(message) } });
    try {
        let form = h.render();
        findElement(form, (el) => typeof el.props?.onChoose === 'function').props.onChoose(prepared);
        form = h.render();
        const field = findElement(form, (el) => el.type === 'textarea');
        assert.equal(field.props.value, 'Здравствуйте, Алия!');
        assert.equal(field.props.readOnly, true);
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).preview, 'Здравствуйте, Алия!');
        form.props.onSubmit();
        assert.equal(queued.length, 1);
        assert.equal(queued[0].text, '[[welcome]][[Алия]]');
        assert.equal(queued[0].displayText, 'Здравствуйте, Алия!');
        assert.equal(findElement(h.render(), (el) => el.type === 'textarea').props.value, '');
        assert.equal(h.storage.size, 0);
    } finally {
        delete globalThis.__wazzupPilotHarness;
        if (previousStorage === undefined) delete globalThis.sessionStorage;
        else globalThis.sessionStorage = previousStorage;
    }
});

test('emoji replaces the selected text and uses UTF-16 cursor position without altering approved templates', () => {
    const previousStorage = globalThis.sessionStorage;
    const previousFrame = globalThis.requestAnimationFrame;
    globalThis.requestAnimationFrame = (callback) => callback();
    const h = createHarness(() => { throw new Error('Choosing emoji must not send a message'); });
    try {
        let form = h.render();
        findElement(form, (el) => el.type === 'textarea').props.onChange({ target: { value: 'Привет, NAME!' } });
        form = h.render();
        findElement(form, (el) => el.type === 'textarea').props.onSelect({ target: { selectionStart: 8, selectionEnd: 12 } });
        findElement(form, (el) => typeof el.props?.onEmoji === 'function').props.onEmoji('👋🏽');
        form = h.render();
        assert.equal(findElement(form, (el) => el.type === 'textarea').props.value, 'Привет, 👋🏽!');
        findElement(form, (el) => typeof el.props?.onEmoji === 'function').props.onEmoji('🙂');
        form = h.render();
        assert.equal(findElement(form, (el) => el.type === 'textarea').props.value, 'Привет, 👋🏽🙂!');
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).text, 'Привет, 👋🏽🙂!');
        findElement(form, (el) => typeof el.props?.onChoose === 'function').props.onChoose({ text: '[[code]]', preview: 'Одобренный текст' });
        form = h.render();
        findElement(form, (el) => typeof el.props?.onEmoji === 'function').props.onEmoji('🙂');
        form = h.render();
        assert.equal(findElement(form, (el) => el.type === 'textarea').props.value, 'Одобренный текст');
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).text, '[[code]]');
    } finally {
        delete globalThis.__wazzupPilotHarness;
        if (previousStorage === undefined) delete globalThis.sessionStorage;
        else globalThis.sessionStorage = previousStorage;
        if (previousFrame === undefined) delete globalThis.requestAnimationFrame;
        else globalThis.requestAnimationFrame = previousFrame;
    }
});

test('local templates are editable text; unsupported or incomplete Wazzup templates cannot be prepared', () => {
    assert.deepEqual(prepareTemplate({ source: 'icore', text: 'Обычный ответ 🙂' }), { text: 'Обычный ответ 🙂', preview: '' });
    assert.deepEqual(prepareTemplate({ source: 'wazzup', kind: 'text', supported: true,
        text: 'Здравствуйте!\nОбычный ответ {{1}}', variables: [], channels: [] }),
    { text: 'Здравствуйте!\nОбычный ответ {{1}}', preview: '' });
    assert.throws(() => prepareTemplate({ source: 'wazzup', supported: false, unsupportedReason: 'Media unsupported' }), /Media unsupported/);
    const item = { source: 'wazzup', supported: true, templateCode: '[[code]][[bodyVar1]]',
        variables: ['bodyVar1'], text: 'Здравствуйте, {{1}}!' };
    for (const value of ['', '   ', '[[injected]]', 'line\nbreak']) {
        assert.throws(() => prepareTemplate(item, { bodyVar1: value }));
    }
    assert.deepEqual(prepareTemplate(item, { bodyVar1: '  Алия  ' }), { text: '[[code]][[Алия]]', preview: 'Здравствуйте, Алия!' });
});

const withHarness = async (post, body, options) => {
    const previousStorage = globalThis.sessionStorage;
    const h = createHarness(post, options);
    try { await body(h); }
    finally {
        h.unmount();
        delete globalThis.__wazzupPilotHarness;
        if (previousStorage === undefined) delete globalThis.sessionStorage;
        else globalThis.sessionStorage = previousStorage;
    }
};
const field = (form) => findElement(form, (el) => el.type === 'textarea');
const action = (form, label) => findElement(form, (el) => el.type === 'button' && el.props['aria-label'] === label);

test('Enter queues once, Shift+Enter preserves a newline and IME composition never submits', async () => {
    const queued = [];
    await withHarness(async () => { throw new Error('The composer itself must not send'); }, async (h) => {
        field(h.render()).props.onChange({ target: { value: 'Добрый день' } });
        const input = field(h.render());
        let prevented = 0;
        const base = { key: 'Enter', preventDefault: () => { prevented += 1; } };
        input.props.onKeyDown({ ...base, shiftKey: true });
        input.props.onKeyDown({ ...base, nativeEvent: { isComposing: true } });
        input.props.onKeyDown({ ...base, nativeEvent: { keyCode: 229 } });
        assert.equal(queued.length, 0);
        assert.equal(prevented, 0);
        input.props.onKeyDown(base);
        input.props.onKeyDown(base);
        assert.equal(queued.length, 1);
        assert.equal(queued[0].text, 'Добрый день');
        assert.equal(prevented, 2);
    }, { props: { onSend: (message) => queued.push(message) } });
});

test('AI translation and paraphrase edit a draft only, support undo and preserve the reply target', async () => {
    const requests = [];
    const replyTo = { messageId: 'original', text: 'Вопрос клиента', authorName: 'Клиент' };
    await withHarness(async (url, body) => {
        requests.push({ url, body });
        return { data: { text: body.action === 'kk' ? 'Сәлеметсіз бе!' : 'Добрый день!' } };
    }, async (h) => {
        field(h.render()).props.onChange({ target: { value: 'привет' } });
        await action(h.render(), 'Қазақшаға аудару').props.onClick();
        let form = h.render();
        assert.equal(field(form).props.value, 'Сәлеметсіз бе!');
        assert.match(requests[0].url, /\/assist$/);
        assert.deepEqual(requests[0].body, { account: 'op', action: 'kk', text: 'привет' });
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).replyTo.messageId, 'original');
        action(form, 'Вернуть исходный текст').props.onClick();
        assert.equal(field(h.render()).props.value, 'привет');
        await action(h.render(), 'Перефразировать').props.onClick();
        assert.equal(requests[1].body.action, 'rewrite');
        assert.equal(field(h.render()).props.value, 'Добрый день!');
        await action(h.render(), 'Перевести на русский').props.onClick();
        assert.equal(requests[2].body.action, 'ru');
        assert.equal(requests.some(({ url }) => url.endsWith('/send')), false);
    }, { props: { replyTo } });
});

test('editing cancels AI and stale completions cannot overwrite newer text or a replacement request', async () => {
    const requests = [];
    await withHarness((url, body, config) => new Promise((resolve) => {
        requests.push({ body, config, resolve });
    }), async (h) => {
        field(h.render()).props.onChange({ target: { value: 'Первый черновик' } });
        const first = action(h.render(), 'Перефразировать').props.onClick();
        assert.equal(action(h.render(), 'Отправить').props.disabled, true);
        field(h.render()).props.onChange({ target: { value: 'Новый черновик' } });
        assert.equal(requests[0].config.signal.aborted, true);
        const second = action(h.render(), 'Перевести на русский').props.onClick();
        requests[0].resolve({ data: { text: 'Устаревший ответ' } });
        await first;
        assert.equal(field(h.render()).props.value, 'Новый черновик');
        assert.equal(action(h.render(), 'Отправить').props.disabled, true);
        requests[1].resolve({ data: { text: 'Актуальный ответ' } });
        await second;
        assert.equal(field(h.render()).props.value, 'Актуальный ответ');
        assert.equal(action(h.render(), 'Отправить').props.disabled, false);
    });
});

test('leaving a chat cancels AI without changing its persisted draft', async () => {
    let request;
    await withHarness((url, body, config) => new Promise((resolve) => { request = { config, resolve }; }), async (h) => {
        field(h.render()).props.onChange({ target: { value: 'Оставить этот черновик' } });
        const pending = action(h.render(), 'Перефразировать').props.onClick();
        h.unmount();
        assert.equal(request.config.signal.aborted, true);
        request.resolve({ data: { text: 'Поздний ответ' } });
        await pending;
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).text, 'Оставить этот черновик');
    });
});

test('approved WABA templates cannot be rewritten or translated and rejected AI keeps original text', async () => {
    let requests = 0;
    await withHarness(async () => { requests += 1; throw { response: { data: { error: 'Сервис временно занят' } } }; }, async (h) => {
        let form = h.render();
        findElement(form, (el) => typeof el.props?.onChoose === 'function').props.onChoose({ text: '[[approved]]', preview: 'Здравствуйте!' });
        form = h.render();
        for (const label of ['Перевести на русский', 'Қазақшаға аудару', 'Перефразировать']) {
            assert.equal(action(form, label).props.disabled, true);
            await action(form, label).props.onClick();
        }
        assert.equal(requests, 0);
        findElement(form, (el) => el.type === 'button' && el.props.children === 'Убрать шаблон').props.onClick();
        field(h.render()).props.onChange({ target: { value: 'Исходный текст' } });
        await action(h.render(), 'Перефразировать').props.onClick();
        assert.equal(field(h.render()).props.value, 'Исходный текст');
        assert.equal(requests, 1);
        assert.ok(findElement(h.render(), (el) => el.props?.role === 'alert'));
    });
});

test('reply target travels with the queued message and is released at once for the next one', async () => {
    const queued = [];
    const cleared = [];
    const selected = { messageId: 'reply-original', text: 'Первый вопрос', authorName: 'Клиент' };
    await withHarness(async () => { throw new Error('The composer itself must not send'); }, async (h) => {
        field(h.render()).props.onChange({ target: { value: 'Ответ на первый вопрос' } });
        h.render().props.onSubmit();
        assert.equal(queued[0].replyToMessageId, 'reply-original');
        assert.deepEqual(queued[0].reply, { text: 'Первый вопрос', authorName: 'Клиент' });
        assert.deepEqual(cleared, ['reply-original']);
        h.render({ replyTo: null });
        field(h.render()).props.onChange({ target: { value: 'Без цитаты' } });
        h.render().props.onSubmit();
        assert.equal(queued[1].replyToMessageId, undefined);
        assert.equal(findElement(h.render(), (el) => el.props?.['data-testid'] === 'wazzup-reply-preview'), null);
    }, { props: { replyTo: selected, onSend: (message) => queued.push(message), onCancelReply: (id) => cleared.push(id) } });
});

const fileInput = (form) => findElement(form, (el) => el.type === 'input' && el.props.type === 'file');
const testFile = () => new File(['%PDF-1.7\nsynthetic test only'], 'test-only.pdf', { type: 'application/pdf' });
const attachmentFixture = {
    id: '8f49c2ce-349f-4e91-92dc-a32b0c4f04b5', name: 'test-only.pdf', size: 29,
    mime: 'application/pdf', expiresAt: '2099-01-01T00:00:00Z',
};
const chooseTestFile = (h, file = testFile()) => fileInput(h.render()).props.onChange({ target: { files: [file], value: 'chosen' } });

test('paperclip uploads a file without sending, locks conflicting tools, and stores only safe metadata', async () => {
    const requests = [];
    let finishUpload;
    await withHarness((url, body, config) => {
        requests.push({ url, body, config });
        return new Promise((resolve) => { finishUpload = resolve; });
    }, async (h) => {
        assert.equal(requests.length, 0);
        field(h.render()).props.onChange({ target: { value: 'Черновик после файла' } });
        const originalForm = h.render();
        const upload = chooseTestFile(h);
        assert.equal(requests.length, 1);
        const { url, body, config } = requests[0];
        assert.match(url, /\/uploads$/);
        assert.equal(body.get('account'), 'op');
        assert.equal(body.get('channelId'), chat.channelId);
        assert.equal(body.get('chatId'), chat.chatId);
        assert.match(body.get('clientUploadId'), /^[0-9a-f-]{36}$/);
        assert.equal(body.get('file').name, 'test-only.pdf');
        assert.equal(config.headers['Content-Type'], undefined);
        assert.equal(config.headers.Authorization, 'test');
        assert.equal(field(h.render()).props.readOnly, true);
        assert.equal(action(h.render(), 'Отправить').props.disabled, true);
        assert.equal(action(h.render(), 'Перефразировать').props.disabled, true);
        assert.equal(findElement(h.render(), (el) => typeof el.props?.onChoose === 'function').props.locked, true);
        await originalForm.props.onSubmit();
        await chooseTestFile(h);
        assert.equal(requests.length, 1);
        finishUpload({ data: { attachment: { ...attachmentFixture, contentUri: 'https://private.invalid/file', blob: 'not persisted' } } });
        await upload;
        const stored = JSON.parse(h.storage.get(pilotDraftStorageKey(chat)));
        assert.deepEqual(stored.attachment, attachmentFixture);
        assert.equal(stored.text, 'Черновик после файла');
        assert.equal(stored.pending, null);
        assert.equal(requests.length, 1);
        assert.equal(action(h.render(), 'Отправить').props.disabled, false);
        action(h.render(), 'Убрать файл').props.onClick();
        assert.equal(field(h.render()).props.value, 'Черновик после файла');
        assert.equal(field(h.render()).props.readOnly, false);
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).attachment, null);
    }, { props: { headers: () => ({ 'Content-Type': 'application/json', Authorization: 'test' }) } });
    const html = render();
    assert.ok(html.indexOf('Прикрепить файл') < html.indexOf('Шаблоны'));
});

test('sending an attachment queues no caption or template text and preserves the independent text draft', async () => {
    const requests = [];
    const queued = [];
    const cleared = [];
    await withHarness(async (url, body) => {
        requests.push({ url, body });
        return { data: { attachment: attachmentFixture } };
    }, async (h) => {
        field(h.render()).props.onChange({ target: { value: 'Отдельное сообщение 🙂' } });
        await chooseTestFile(h);
        const form = h.render();
        const tools = findElement(form, (el) => typeof el.props?.onChoose === 'function');
        tools.props.onChoose({ text: '[[not-allowed]]', preview: 'Шаблон' });
        tools.props.onEmoji('🙂');
        field(form).props.onChange({ target: { value: 'Не заменять черновик' } });
        assert.equal(field(h.render()).props.value, 'Отдельное сообщение 🙂');
        h.render().props.onSubmit();
        assert.equal(requests.length, 1, 'only the upload; the send belongs to the queue');
        assert.equal(queued.length, 1);
        assert.equal(queued[0].attachmentId, attachmentFixture.id);
        assert.equal(queued[0].text, '');
        assert.equal(queued[0].displayText, 'test-only.pdf');
        assert.deepEqual(queued[0].attachment, { name: 'test-only.pdf', size: 29, mime: 'application/pdf' });
        assert.equal(queued[0].contentUri, undefined);
        assert.equal(queued[0].replyToMessageId, 'reply-file');
        assert.deepEqual(cleared, ['reply-file']);
        assert.equal(field(h.render()).props.value, 'Отдельное сообщение 🙂');
        assert.equal(field(h.render()).props.readOnly, false);
        assert.equal(findElement(h.render(), (el) => el.props?.['data-testid'] === 'wazzup-file-preview'), null);
        const stored = JSON.parse(h.storage.get(pilotDraftStorageKey(chat)));
        assert.equal(stored.text, 'Отдельное сообщение 🙂');
        assert.equal(stored.attachment, null);
        assert.equal(stored.pending, null);
    }, { props: { replyTo: { messageId: 'reply-file', text: 'Вопрос' }, onSend: (message) => queued.push(message),
        onCancelReply: (id) => cleared.push(id) } });
});

test('removing a file or leaving a chat aborts upload and ignores late completion without overwriting the draft', async () => {
    const uploads = [];
    await withHarness((url, body, config) => new Promise((resolve) => { uploads.push({ config, resolve }); }), async (h) => {
        field(h.render()).props.onChange({ target: { value: 'Оставить текст' } });
        const first = chooseTestFile(h);
        action(h.render(), 'Убрать файл').props.onClick();
        assert.equal(uploads[0].config.signal.aborted, true);
        const second = chooseTestFile(h);
        uploads[0].resolve({ data: { attachment: attachmentFixture } });
        await first;
        assert.equal(action(h.render(), 'Отправить').props.disabled, true);
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).attachment, null);
        h.unmount();
        assert.equal(uploads[1].config.signal.aborted, true);
        uploads[1].resolve({ data: { attachment: attachmentFixture } });
        await second;
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).attachment, null);
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).text, 'Оставить текст');
    });
});

test('a double Enter after a file sends the file once and keeps the draft; a deliberate Enter later sends the draft', async () => {
    const queued = [];
    const realNow = Date.now;
    let now = 1_000_000;
    Date.now = () => now;
    try {
        await withHarness(async (url) => {
            if (url.endsWith('/uploads')) return { data: { attachment: attachmentFixture } };
            throw new Error('The composer itself must not send');
        }, async (h) => {
            field(h.render()).props.onChange({ target: { value: 'Черновик, не отправлять' } });
            await chooseTestFile(h);
            const input = field(h.render());
            const enter = { key: 'Enter', preventDefault() {} };
            input.props.onKeyDown(enter);
            input.props.onKeyDown(enter);              // same tick (auto-repeat)
            field(h.render()).props.onKeyDown(enter);  // next render, still within the guard
            assert.deepEqual(queued.map((m) => [m.attachmentId || null, m.text]), [[attachmentFixture.id, '']]);
            assert.equal(field(h.render()).props.value, 'Черновик, не отправлять');
            now += 1100;
            field(h.render()).props.onKeyDown(enter);
            assert.deepEqual(queued.map((m) => [m.attachmentId || null, m.text]),
                [[attachmentFixture.id, ''], [null, 'Черновик, не отправлять']]);
        }, { props: { onSend: (message) => queued.push(message) } });
    } finally {
        Date.now = realNow;
    }
});

test('«Изменить» returns a rejected text to the field once, in front of what was typed since', async () => {
    const restored = [];
    await withHarness(async () => { throw new Error('No request expected'); }, async (h) => {
        h.render({ restore: { id: 'failed-1', text: 'Отклонённый текст' } });
        assert.equal(field(h.render()).props.value, 'Отклонённый текст');
        assert.deepEqual(restored, ['failed-1']);
        field(h.render()).props.onChange({ target: { value: 'Новое' } });
        h.render({ restore: { id: 'failed-1', text: 'Отклонённый текст' } });
        assert.equal(field(h.render()).props.value, 'Новое', 'the same restore is applied once');
        h.render({ restore: { id: 'failed-2', text: 'Второй' } });
        assert.equal(field(h.render()).props.value, 'Второй\nНовое');
        assert.equal(JSON.parse(h.storage.get(pilotDraftStorageKey(chat))).text, 'Второй\nНовое');
        assert.deepEqual(restored, ['failed-1', 'failed-2']);
    }, { props: { onRestored: (id) => restored.push(id) } });
});

test('expired unsent attachment produces a clear error, is never queued and stays removable', async () => {
    const storage = new Map([[pilotDraftStorageKey(chat), JSON.stringify({ text: 'Черновик',
        attachment: { ...attachmentFixture, expiresAt: '2020-01-01T00:00:00Z' } })]]);
    const queued = [];
    await withHarness(() => { throw new Error('Expired file must not be uploaded again'); }, async (h) => {
        h.render().props.onSubmit();
        assert.equal(queued.length, 0);
        assert.ok(findElement(h.render(), (el) => el.props?.role === 'alert'));
        assert.equal(action(h.render(), 'Убрать файл').props.disabled, undefined);
        action(h.render(), 'Убрать файл').props.onClick();
        assert.equal(field(h.render()).props.value, 'Черновик');
    }, { storage, props: { onSend: (message) => queued.push(message) } });
});

test('unsupported, empty and oversized files are rejected before upload; approved templates cannot accept files', async () => {
    assert.equal(uploadFileError({ name: 'photo.PNG', size: MAX_UPLOAD_IMAGE_BYTES }), '');
    assert.equal(uploadFileError({ name: 'report.PDF', size: MAX_UPLOAD_BYTES }), '');
    assert.match(uploadFileError({ name: 'photo.jpg', size: MAX_UPLOAD_IMAGE_BYTES + 1 }), /5 МБ/);
    assert.match(uploadFileError({ name: 'video.mp4', size: MAX_UPLOAD_BYTES + 1 }), /10 МБ/);
    assert.equal(uploadedAttachment({ ...attachmentFixture, expiresAt: { secret: 'omit' } }).expiresAt, null);
    let requests = 0;
    await withHarness(() => { requests += 1; throw new Error('Must not upload'); }, async (h) => {
        for (const file of [{ name: 'test.exe', size: 2 }, { name: 'empty.pdf', size: 0 },
            { name: 'photo.jpg', size: MAX_UPLOAD_IMAGE_BYTES + 1 }, { name: 'file.pdf', size: MAX_UPLOAD_BYTES + 1 }]) {
            await chooseTestFile(h, file);
            assert.ok(findElement(h.render(), (el) => el.props?.role === 'alert'));
        }
        findElement(h.render(), (el) => typeof el.props?.onChoose === 'function').props.onChoose({ text: '[[approved]]', preview: 'Шаблон' });
        assert.equal(action(h.render(), 'Прикрепить файл').props.disabled, true);
        await chooseTestFile(h);
        assert.equal(requests, 0);
        assert.equal(field(h.render()).props.value, 'Шаблон');
    });
});
