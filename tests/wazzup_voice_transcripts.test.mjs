import test, { mock } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

/* Текст голосовых в ленте «Чатов ОП»: что спрашивать у сервера, что опрашивать,
 * как показывать (useVoiceTranscripts, voiceTranscripts.js, ChatVoiceTranscript). */
const require = createRequire(import.meta.url);
const React = require('react');
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const harnessPlugin = { name: 'voice-harness', setup(builder) {
    builder.onResolve({ filter: /^(react|axios)$/ }, ({ path }) => ({ path, namespace: 'voice-fixture' }));
    builder.onLoad({ filter: /.*/, namespace: 'voice-fixture' }, ({ path }) => ({ loader: 'js', contents:
        path === 'react' ? `const h=()=>globalThis.__voiceHarness;
            export const useState=(...v)=>h().useState(...v);
            export const useRef=(...v)=>h().useRef(...v);
            export const useEffect=(...v)=>h().useEffect(...v);
            export const useCallback=(...v)=>h().useCallback(...v);
            export const Fragment=Symbol.for('react.fragment');
            export default {createElement:(...v)=>h().createElement(...v),Fragment:Symbol.for('react.fragment')};`
            : 'export default {post:(...v)=>globalThis.__voiceHarness.post(...v)};',
    }));
} };
async function load(name) {
    const outfile = join(cache, `voice-${name.replace(/\W/g, '_')}.mjs`);
    await build({ entryPoints: [join(process.cwd(), `src/components/wazzup/${name}`)], outfile,
        bundle: true, platform: 'node', format: 'esm', target: 'node22', external: ['lucide-react'], plugins: [harnessPlugin] });
    return import(pathToFileURL(outfile));
}
const rules = await load('voiceTranscripts.js');
const { default: useVoice } = await load('useVoiceTranscripts.js');
const { default: Transcript } = await load('ChatVoiceTranscript.jsx');
const tick = () => new Promise((resolve) => setImmediate(resolve));
const defer = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

function fixture({ post, props: extra = {} } = {}) {
    const slots = [], posts = [];
    let index = 0, effects = [];
    const voice = (id) => ({ messageId: id, type: 'audio', contentUri: `https://store.wazzup24.com/${id}` });
    const props = { account: 'op', apiBaseUrl: '/api-base', headers: () => ({ Authorization: 'token' }),
        chat: { channelId: 'channel', chatId: 'chat' },
        thread: [voice('v1'), { messageId: 't1', type: 'text', text: 'Сәлем' }, voice('v2')], ...extra };
    const h = {
        voice,
        createElement: React.createElement,
        post(...args) { posts.push(args); return post(...args); },
        useState(initial) { const i = index++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
            return [slots[i].value, (value) => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }]; },
        useRef(initial) { const i = index++; slots[i] ??= { current: initial }; return slots[i]; },
        useCallback(fn, deps) { const i = index++; if (!slots[i] || deps.some((value, k) => !Object.is(value, slots[i].deps[k]))) slots[i] = { fn, deps }; return slots[i].fn; },
        useEffect(fn, deps) { const i = index++, old = slots[i];
            if (old && deps?.every((value, k) => Object.is(value, old.deps[k]))) return;
            slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); },
        render(next = {}) { Object.assign(props, next); index = 0; const result = useVoice(props);
            const pending = effects; effects = []; pending.forEach((fn) => fn()); return result; },
        unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
        posts,
    };
    globalThis.__voiceHarness = h;
    return h;
}

test('only archived voice messages are asked about; a new chat draft has no key', () => {
    const thread = [{ messageId: 'a', type: 'audio', contentUri: 'u' }, { messageId: 'b', type: 'audio' },
        { messageId: 'c', type: 'audio', contentUri: 'u', local: { state: 'sending' } }, { messageId: 'd', type: 'image', contentUri: 'u' }];
    assert.deepEqual(rules.voiceMessageIds(thread), ['a']);
    assert.equal(rules.voiceTranscriptKey('op', { channelId: 'x', chatId: 'y', isNew: true }), '');
    assert.equal(rules.voiceTranscriptKey('op', { channelId: 'x', chatId: 'y' }), '["op","x","y"]');
});

test('an answer replaces what was known and silence about an id means no text will come', () => {
    const merged = rules.mergeVoiceTranscripts({ a: { status: 'pending', text: '' } }, ['a', 'b'],
        [{ messageId: 'a', status: 'ready', text: 'Алло' }, { messageId: 'x', status: 'weird' }]);
    assert.deepEqual(merged, { a: { status: 'ready', text: 'Алло' }, b: { status: 'none', text: '' } });
    assert.deepEqual(rules.pendingIds(['a', 'b'], { a: { status: 'pending' }, b: { status: 'ready' } }), ['a']);
    assert.equal(rules.pollDelay(0), 2000);
    assert.ok(rules.pollDelay(rules.FAST_ROUNDS - 1) <= 8000);
    assert.equal(rules.pollDelay(rules.FAST_ROUNDS), 30000);
});

test('the open chat asks once for its voice messages, then polls only the pending ones until ready', async () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    const answers = [
        { items: [{ messageId: 'v1', status: 'ready', text: 'Сәлеметсіз бе' }, { messageId: 'v2', status: 'pending', text: null }] },
        { items: [{ messageId: 'v2', status: 'pending', text: null }] },
        { items: [{ messageId: 'v2', status: 'ready', text: 'Где машина?' }] },
    ];
    const h = fixture({ post: () => Promise.resolve({ data: answers[h.posts.length - 1] }) });
    try {
        h.render(); await tick();
        assert.equal(h.posts.length, 1);
        const [url, body, config] = h.posts[0];
        assert.equal(url, '/api-base/api/wazzup/voice-transcripts');
        // Свежие — первыми.
        assert.deepEqual(body, { channelId: 'channel', chatId: 'chat', messageIds: ['v2', 'v1'], retry: [] });
        assert.deepEqual(config.params, { account: 'op' });
        assert.equal(config.headers.Authorization, 'token');
        assert.equal(h.render().byId.v1.text, 'Сәлеметсіз бе');
        mock.timers.tick(rules.pollDelay(0)); await tick();
        assert.deepEqual(h.posts[1][1].messageIds, ['v2']);
        mock.timers.tick(rules.pollDelay(1)); await tick();
        assert.equal(h.render().byId.v2.text, 'Где машина?');
        mock.timers.tick(60000); await tick();
        assert.equal(h.posts.length, 3);
        h.render(); await tick();             // a re-render without new voice asks nothing
        assert.equal(h.posts.length, 3);
    } finally { h.unmount(); mock.timers.reset(); }
});

test('a new voice message arriving in the thread is asked about on its own', async () => {
    const h = fixture({ post: (url, body) => Promise.resolve({ data: { items: body.messageIds.map((id) => ({ messageId: id, status: 'ready', text: id })) } }) });
    try {
        h.render(); await tick();
        h.render({ thread: [h.voice('v1'), h.voice('v2'), h.voice('v3')] }); await tick();
        assert.deepEqual(h.posts.map(([, body]) => body.messageIds), [['v2', 'v1'], ['v3']]);
    } finally { h.unmount(); }
});

test('an answer for the previous chat is dropped after switching chats', async () => {
    const first = defer();
    const h = fixture({ post: () => (h.posts.length === 1 ? first.promise
        : Promise.resolve({ data: { items: [{ messageId: 'n1', status: 'ready', text: 'новый чат' }] } })) });
    try {
        h.render(); await tick();
        h.render({ chat: { channelId: 'channel', chatId: 'other' }, thread: [h.voice('n1')] }); await tick();
        first.resolve({ data: { items: [{ messageId: 'v1', status: 'ready', text: 'старый чат' }] } }); await tick();
        const current = h.render();
        assert.equal(current.byId.v1, undefined);
        assert.equal(current.byId.n1.text, 'новый чат');
    } finally { h.unmount(); }
});

test('retry marks the message pending at once and sends it in the retry list', async () => {
    const h = fixture({ post: (url, body) => Promise.resolve({ data: { items: body.messageIds.length
        ? [{ messageId: 'v1', status: 'failed', text: null }, { messageId: 'v2', status: 'ready', text: 'ok' }]
        : [{ messageId: 'v1', status: 'pending', text: null }] } }) });
    try {
        h.render(); await tick();
        assert.equal(h.render().byId.v1.status, 'failed');
        h.render().retry('v1');
        assert.equal(h.render().byId.v1.status, 'pending');
        await tick();
        assert.deepEqual(h.posts[1][1].retry, ['v1']);
    } finally { h.unmount(); }
});

test('a failing route does not get asked again for every new render', async () => {
    const h = fixture({ post: () => Promise.reject(new Error('503')) });
    try {
        h.render(); await tick(); h.render(); await tick();
        assert.equal(h.posts.length, 1);
        assert.equal(h.render().byId.v1.status, 'none');
    } finally { h.unmount(); }
});

test('the bubble shows ready text plainly and service states as one quiet line', () => {
    const focused = [];
    globalThis.__voiceHarness = { createElement: React.createElement,
        useRef: () => ({ current: { focus: () => focused.push('status line') } }) };
    const find = (node, fn) => {
        if (!node || typeof node !== 'object') return null;
        if (fn(node)) return node;
        for (const child of React.Children.toArray(node.props?.children)) { const hit = find(child, fn); if (hit) return hit; }
        return null;
    };
    const text = (node) => (node == null || typeof node === 'boolean' ? '' : typeof node !== 'object' ? String(node)
        : React.Children.toArray(node.props?.children).map(text).join(''));
    const ready = Transcript({ state: { status: 'ready', text: 'Мен жолдамын, 5 минутта жетемін.' } });
    assert.equal(ready.type, 'p');
    assert.equal(text(ready), 'Мен жолдамын, 5 минутта жетемін.');
    assert.match(ready.props.title, /ИИ/);
    assert.doesNotMatch(ready.props.className, /text-(slate|gray|blue)-/);   // the bubble's own colour
    assert.match(text(Transcript({ state: { status: 'pending' } })), /Расшифровываем/);
    assert.match(text(Transcript({ state: { status: 'no_speech' } })), /Речь не разобрать/);
    let retried = 0;
    const failed = Transcript({ state: { status: 'failed' }, onRetry: () => { retried += 1; } });
    // One status element for every service state: focus lands on it after «Повторить».
    assert.equal(failed.props.role, 'status');
    assert.equal(Transcript({ state: { status: 'pending' } }).props.role, 'status');
    find(failed, (node) => node.type === 'button').props.onClick();
    assert.equal(retried, 1);
    assert.deepEqual(focused, ['status line']);
    for (const status of ['unavailable', 'none']) assert.equal(Transcript({ state: { status } }), null);
    assert.equal(Transcript({ state: undefined }), null);
});

test('a long feed is asked in portions the server accepts, newest first', async () => {
    const many = Array.from({ length: rules.VOICE_BATCH + 50 }, (_, i) => ({ messageId: `m${i}`, type: 'audio', contentUri: `u${i}` }));
    const h = fixture({ props: { thread: many }, post: (url, body) => Promise.resolve({ data: { items:
        body.messageIds.map((id) => ({ messageId: id, status: 'ready', text: id })) } }) });
    try {
        h.render(); await tick(); await tick(); await tick();
        assert.equal(h.posts.length, 2);
        assert.equal(h.posts[0][1].messageIds.length, rules.VOICE_BATCH);
        assert.equal(h.posts[0][1].messageIds[0], `m${rules.VOICE_BATCH + 49}`);
        assert.equal(h.posts[1][1].messageIds.length, 50);
        assert.equal(Object.keys(h.render().byId).length, rules.VOICE_BATCH + 50);
    } finally { h.unmount(); }
});

test('waiting goes on slowly while the chat is open, restarts on progress and ends without a spinner', async () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    let ready = false;
    const h = fixture({ props: { thread: [{ messageId: 'v1', type: 'audio', contentUri: 'u' }] },
        post: () => Promise.resolve({ data: { items: [{ messageId: 'v1', status: ready ? 'ready' : 'pending', text: ready ? 'Келдім' : null }] } }) });
    const run = async (rounds) => { for (let i = 0; i < rounds; i += 1) { mock.timers.tick(30000); await tick(); } };
    try {
        h.render(); await tick();
        await run(rules.FAST_ROUNDS + 10);               // past the old 4.7-minute stop
        assert.equal(h.render().byId.v1.status, 'pending');
        const asked = h.posts.length;
        ready = true; await run(1);
        assert.equal(h.render().byId.v1.text, 'Келдім');
        assert.equal(h.posts.length, asked + 1);
        await run(5);
        assert.equal(h.posts.length, asked + 1);        // nothing left to wait for
    } finally { h.unmount(); mock.timers.reset(); }
});

test('after the longest wait the line goes instead of spinning forever', async () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    const h = fixture({ props: { thread: [{ messageId: 'v1', type: 'audio', contentUri: 'u' }] },
        post: () => Promise.resolve({ data: { items: [{ messageId: 'v1', status: 'pending', text: null }] } }) });
    try {
        h.render(); await tick();
        for (let i = 0; i <= rules.POLL_ROUNDS + 2; i += 1) { mock.timers.tick(30000); await tick(); }
        assert.equal(h.render().byId.v1.status, 'none');
        assert.equal(h.posts.length, rules.POLL_ROUNDS + 1);
        let total = 0; for (let r = 0; r < rules.POLL_ROUNDS; r += 1) total += rules.pollDelay(r);
        assert.ok(total > 25 * 60 * 1000, 'covers the server retry schedule (20 s, 2 and 10 min) with a backlog');
    } finally { h.unmount(); mock.timers.reset(); }
});
