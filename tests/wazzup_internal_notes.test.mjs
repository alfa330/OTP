import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const harnessPlugin = { name: 'internal-notes-harness', setup(builder) {
    builder.onResolve({ filter: /^(react|axios)$/ }, ({ path }) => ({ path, namespace: 'notes-fixture' }));
    builder.onLoad({ filter: /.*/, namespace: 'notes-fixture' }, ({ path }) => ({ loader: 'js', contents:
        path === 'react' ? `const h=()=>globalThis.__notesHarness;
            export const memo=(component)=>component;
            export const useState=(...v)=>h().useState(...v);
            export const useRef=(...v)=>h().useRef(...v);
            export const useEffect=(...v)=>h().useEffect(...v);
            export const useCallback=(...v)=>h().useCallback(...v);
            export default {createElement:(...v)=>h().createElement(...v)};`
            : 'export default {get:(...v)=>globalThis.__notesHarness.get(...v),post:(...v)=>globalThis.__notesHarness.post(...v)};',
    }));
} };
async function load(name) {
    const outfile = join(cache, `${name}.test.mjs`);
    await build({ entryPoints: [join(process.cwd(), `src/components/wazzup/${name}`)], outfile,
        bundle: true, platform: 'node', format: 'esm', target: 'node22', external: ['lucide-react'], plugins: [harnessPlugin] });
    return import(pathToFileURL(outfile));
}
const { default: useNotes, mergeInternalNotes } = await load('useInternalNotes.js');
const { default: Composer, InternalNoteDraft } = await load('ChatInternalNoteComposer.jsx');
const { default: Note } = await load('ChatInternalNote.jsx');
const defer = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const tick = () => new Promise((resolve) => setImmediate(resolve));
const note = (id, text = 'Только для коллег') => ({ id, text, authorName: 'Оператор', authorId: 7, createdAt: '2026-10-08T10:00:00Z' });
const find = (tree, fn) => {
    if (!tree || typeof tree !== 'object') return null;
    if (fn(tree)) return tree;
    for (const child of React.Children.toArray(tree.props?.children)) { const result = find(child, fn); if (result) return result; }
    return null;
};
const field = (tree) => find(tree, (node) => node.type === 'textarea');

function fixture(Component, { get, post, props: extra = {}, storage = new Map() } = {}) {
    const slots = [], requests = [], posts = [], saved = [];
    let index = 0, effects = [], unmounted = false, lateWrites = 0;
    const props = { enabled: true, apiBaseUrl: '/fixture', headers: () => ({ Authorization: 'fixture' }),
        chat: { channelId: 'channel', chatId: 'chat' }, onSaved: (item) => saved.push(item), ...extra };
    const previousStorage = globalThis.sessionStorage;
    globalThis.sessionStorage = { getItem: (key) => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) };
    const h = {
        createElement: React.createElement,
        get(...args) { requests.push(args); return get?.(...args) || Promise.resolve({ data: { items: [] } }); },
        post(...args) { posts.push(args); return post?.(...args) || Promise.resolve({ data: { item: note('saved') } }); },
        useState(initial) { const i = index++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
            return [slots[i].value, (value) => { if (unmounted) lateWrites += 1;
                slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }]; },
        useRef(initial) { const i = index++; slots[i] ??= { current: initial }; return slots[i]; },
        useCallback(fn, deps) { const i = index++; if (!slots[i] || deps.some((value, k) => !Object.is(value, slots[i].deps[k]))) slots[i] = { fn, deps }; return slots[i].fn; },
        useEffect(fn, deps) { const i = index++, old = slots[i];
            if (old && deps?.every((value, k) => Object.is(value, old.deps[k]))) return;
            slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); },
        render(next = {}) { Object.assign(props, next); index = 0; const result = Component(props);
            const pending = effects; effects = []; pending.forEach((fn) => fn()); return result; },
        unmount() { slots.forEach((slot) => slot?.cleanup?.()); unmounted = true; },
        restore() { if (!unmounted) this.unmount(); globalThis.sessionStorage = previousStorage; delete globalThis.__notesHarness; },
        requests, posts, saved, storage, get lateWrites() { return lateWrites; },
    };
    globalThis.__notesHarness = h;
    return h;
}

test('live comments merge with an in-flight snapshot, ignore other chats and deduplicate without HTTP echo', async () => {
    const response = defer();
    const h = fixture(useNotes, { get: () => response.promise });
    try {
        const first = h.render();
        first.apply({ account: 'op', channelId: 'channel', chatId: 'chat', note: note('2') });
        first.apply({ account: 'op', channelId: 'channel', chatId: 'other', note: note('foreign') });
        response.resolve({ data: { items: [note('1'), note('2')] } }); await tick();
        const current = h.render({ headers: () => ({ Authorization: 'refreshed' }) });
        assert.deepEqual(current.items.map((item) => item.id), ['1', '2']);
        assert.equal(current.loading, false); assert.equal(h.requests.length, 1);
        current.apply({ account: 'op', channelId: 'channel', chatId: 'chat', note: note('2') });
        assert.equal(h.render().items.length, 2); assert.equal(h.requests.length, 1);
        assert.match(h.requests[0][0], /\/notes$/);
        assert.deepEqual(h.requests[0][1].params, { account: 'op', channelId: 'channel', chatId: 'chat' });
    } finally { h.restore(); }
});

test('changing or disabling the chat aborts old reads and suppresses stale responses and old SSE callbacks', async () => {
    const first = defer(), second = defer(); let reads = 0;
    const h = fixture(useNotes, { get: () => (++reads === 1 ? first.promise : second.promise) });
    try {
        const stale = h.render();
        const changed = h.render({ chat: { channelId: 'channel', chatId: 'next' } });
        assert.deepEqual(changed.items, []); assert.equal(h.requests[0][1].signal.aborted, true);
        stale.apply({ account: 'op', channelId: 'channel', chatId: 'chat', note: note('old-live') });
        first.resolve({ data: { items: [note('old')] } }); await tick();
        second.resolve({ data: { items: [note('new')] } }); await tick();
        assert.deepEqual(h.render().items.map((item) => item.id), ['new']);
        const inactive = h.render({ enabled: false });
        assert.deepEqual(inactive.items, []); assert.equal(inactive.loading, false);
        await inactive.refresh(); assert.equal(h.requests.length, 2);
    } finally { h.restore(); }
});

test('reconnect refresh preserves loaded notes on failure and closing aborts the read', async () => {
    const response = defer(); let reads = 0;
    const h = fixture(useNotes, { get: () => (++reads === 1 ? Promise.resolve({ data: { items: [note('1')] } }) : response.promise) });
    try {
        h.render(); await tick(); const work = h.render().refresh();
        response.reject(new Error('offline')); await work;
        assert.equal(h.render().items.length, 1); assert.match(h.render().error, /Не удалось/);
        h.render().refresh(); h.unmount(); await tick();
        assert.equal(h.requests.at(-1)[1].signal.aborted, true); assert.equal(h.lateWrites, 0);
    } finally { h.restore(); }
});

test('note window is bounded to 200, timestamp ordered, and malformed entries are ignored', () => {
    const items = Array.from({ length: 205 }, (_, i) => note(String(i)));
    assert.deepEqual(mergeInternalNotes([], [...items, null, { id: 'bad' }]).map((item) => item.id), items.slice(-200).map((item) => item.id));
});

test('saving uses only the internal notes route and same-tick submits create one request', async () => {
    const response = defer(); const h = fixture(InternalNoteDraft, { post: () => response.promise });
    try {
        field(h.render()).props.onChange({ target: { value: '  Коллеги, проверьте документы.  ' } });
        const tree = h.render(); const work = tree.props.onSubmit(); await tree.props.onSubmit();
        assert.equal(h.posts.length, 1); assert.match(h.posts[0][0], /\/pilot\/notes$/);
        assert.equal(h.posts[0][1].text, 'Коллеги, проверьте документы.');
        assert.deepEqual(Object.keys(h.posts[0][1]).sort(), ['account', 'channelId', 'chatId', 'clientNoteId', 'text']);
        assert.match(h.posts[0][1].clientNoteId, /^[a-f0-9-]{36}$/);
        assert.equal(h.requests.length, 0);
        response.resolve({ data: { item: note('saved') } }); await work;
        assert.equal(h.saved.length, 1); assert.equal(h.storage.size, 0);
        assert.equal(field(h.render()).props.value, '');
    } finally { h.restore(); }
});

test('uncertain save retains the text and idempotency key across retries and composer remount', async () => {
    const storage = new Map(); let firstId;
    let h = fixture(InternalNoteDraft, { storage, post: () => Promise.reject(new Error('timeout')) });
    try {
        field(h.render()).props.onChange({ target: { value: 'Не отправлять водителю' } });
        await h.render().props.onSubmit(); firstId = h.posts[0][1].clientNoteId;
        const failed = h.render(); assert.equal(field(failed).props.readOnly, true);
        field(failed).props.onChange({ target: { value: 'Нельзя менять неизвестный результат' } });
        assert.equal(field(h.render()).props.value, 'Не отправлять водителю');
    } finally { h.restore(); }
    let retryCount = 0;
    h = fixture(InternalNoteDraft, { storage, post: () => ++retryCount === 1
        ? Promise.reject({ response: { status: 403, data: { error: 'Доступ временно недоступен' } } })
        : Promise.resolve({ data: { item: note('saved') } }) });
    try {
        const restored = h.render(); assert.equal(field(restored).props.value, 'Не отправлять водителю');
        await restored.props.onSubmit();
        assert.equal(field(h.render()).props.readOnly, true, 'lost access does not prove the original write failed');
        await h.render().props.onSubmit();
        assert.equal(h.posts[0][1].clientNoteId, firstId); assert.equal(h.posts[1][1].clientNoteId, firstId);
        assert.equal(h.saved.length, 1);
    } finally { h.restore(); }
});

test('closing pending save aborts it, keeps a recoverable draft and never writes late UI state', async () => {
    const response = defer(); const h = fixture(InternalNoteDraft, { post: () => response.promise });
    try {
        field(h.render()).props.onChange({ target: { value: 'Черновик' } });
        const work = h.render().props.onSubmit(); h.unmount();
        assert.equal(h.posts[0][2].signal.aborted, true);
        response.reject(new Error('aborted')); await work;
        assert.equal(h.lateWrites, 0); assert.equal(h.saved.length, 0);
        assert.equal(JSON.parse([...h.storage.values()][0]).pending.clientNoteId, h.posts[0][1].clientNoteId);
    } finally { h.restore(); }
});

test('definitive validation errors permit editing; Enter saves, Shift+Enter and IME do not', async () => {
    const h = fixture(InternalNoteDraft, { post: () => Promise.reject({ response: { status: 400, data: { error: 'Ошибка' } } }) });
    try {
        field(h.render()).props.onChange({ target: { value: 'Текст' } });
        const keydown = field(h.render()).props.onKeyDown;
        keydown({ key: 'Enter', shiftKey: true }); keydown({ key: 'Enter', nativeEvent: { isComposing: true } });
        assert.equal(h.posts.length, 0);
        let prevented = false; keydown({ key: 'Enter', preventDefault() { prevented = true; } }); await tick();
        assert.equal(prevented, true); assert.equal(h.posts.length, 1); assert.equal(field(h.render()).props.readOnly, false);
        field(h.render()).props.onChange({ target: { value: 'Исправленный текст' } });
        await h.render().props.onSubmit();
        assert.notEqual(h.posts[0][1].clientNoteId, h.posts[1][1].clientNoteId);
    } finally { h.restore(); }
});

test('composer is keyed by conversation and note renderer has no send, reply or HTML injection handlers', () => {
    const h = fixture(Composer);
    try {
        const first = h.render(); const second = h.render({ chat: { channelId: 'channel', chatId: 'next' } });
        assert.notEqual(first.key, second.key);
        const tree = Note({ note: note('1', '<img src=x onerror=alert(1)>') });
        assert.equal(find(tree, (node) => node.props?.onDoubleClick || node.props?.onClick || node.props?.dangerouslySetInnerHTML), null);
        const rendered = require('react-dom/server').renderToStaticMarkup(tree);
        assert.match(rendered, /&lt;img src=x onerror=alert\(1\)&gt;/);
        assert.doesNotMatch(rendered, /<img src="x"/);
    } finally { h.restore(); }
});
