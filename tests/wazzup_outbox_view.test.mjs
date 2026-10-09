import test, { mock } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

// Executes the real outbox wiring of WazzupChatsView.jsx (queue subscription,
// local bubbles in the thread, settle, fallback re-read, failure notices,
// «Изменить») against the real send queue, with deterministic hooks, an HTTP
// double and a virtual clock. Nothing leaves the process.
const require = createRequire(import.meta.url);
const { build } = require('esbuild');
const source = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8').replace(/\r\n/g, '\n');
const between = (start, end) => {
    const first = source.indexOf(start);
    const last = source.indexOf(end, first);
    assert.ok(first >= 0 && last > first, `view fragment: ${start}`);
    return source.slice(first, last);
};
const outboxBlock = between('    const outbox = useChatOutbox(', '    const pilotView = useRef({});');
const threadBlock = between('    // Группировка ленты по дням', '    const lastIncomingMessageId');
const effectsBlock = between('    const acceptedIds = ', '    /* Своё новое сообщение всегда видно');
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzup-outbox-view.mjs');
await build({
    stdin: { contents: `
        import { outboxMessages, pilotChatKey } from './chatPilot';
        import { chatKeyOf, configureSendQueue, enqueueMessage, outboxProblems, outboxSnapshot, resetSendQueueForTests,
            settleOutbox, takeBackMessage, useChatOutbox, useOutbox } from './sendQueue';
        import { localDayKey } from './messageTime';
        import { messageQuote } from './threadPresentation';
        import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
        const fmtDay = (iso) => 'day ' + String(iso).slice(0, 10);
        export { configureSendQueue, enqueueMessage, outboxSnapshot, resetSendQueueForTests };
        export default function Wiring({ thread, account, mayProcess, selectedKey, notes, pilot, mainTab,
            refreshPilotThread, loadChats, showToast, chats }) {
            const attachmentThread = useRef([]);
            ${outboxBlock}
            const pilotView = useRef({});
            pilotView.current = { key: selectedKey, thread, mainTab };
            ${threadBlock}
            ${effectsBlock}
            return { localMessages, threadWithDays, threadQuotes, problemChats, sendProblems, editFailed, draftRestore };
        }
    `, resolveDir: join(process.cwd(), 'src/components/wazzup'), loader: 'jsx' },
    outfile: output, bundle: true, platform: 'node', format: 'esm',
    plugins: [{ name: 'outbox-view-doubles', setup(builder) {
        builder.onResolve({ filter: /^(react|axios)$/ }, ({ path }) => ({ path, namespace: 'outbox-double' }));
        builder.onLoad({ filter: /.*/, namespace: 'outbox-double' }, ({ path }) => ({ loader: 'js', contents: path === 'react'
            ? `const h=()=>globalThis.__outboxHooks;
               export const useState=(...a)=>h().useState(...a); export const useRef=(...a)=>h().useRef(...a);
               export const useEffect=(...a)=>h().useEffect(...a); export const useMemo=(...a)=>h().useMemo(...a);
               export const useCallback=(fn,deps)=>h().useMemo(()=>fn,deps);
               export const useSyncExternalStore=(subscribe,get)=>get();`
            : 'export default {post:(...args)=>globalThis.__outboxPost(...args)};' }));
    } }],
});
const view = await import(pathToFileURL(output));

const same = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
function mount(initial) {
    const slots = [];
    let index = 0;
    let pending = [];
    const props = { account: 'op', mayProcess: true, notes: { items: [] }, pilot: { connection: 'live' }, mainTab: 'chats',
        chats: [], refreshPilotThread: async () => {}, loadChats: async () => {}, showToast: () => {}, ...initial };
    globalThis.__outboxHooks = {
        useState(init) {
            const i = index++;
            slots[i] ??= { value: typeof init === 'function' ? init() : init };
            return [slots[i].value, (next) => { slots[i].value = typeof next === 'function' ? next(slots[i].value) : next; }];
        },
        useRef(init) { const i = index++; return (slots[i] ??= { current: init }); },
        useMemo(fn, deps) {
            const i = index++;
            if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { deps, value: fn() };
            return slots[i].value;
        },
        useEffect(fn, deps) {
            const i = index++;
            const old = slots[i];
            if (old && deps && same(old.deps, deps)) return;
            slots[i] = { deps };
            pending.push(() => { old?.cleanup?.(); const cleanup = fn(); slots[i].cleanup = typeof cleanup === 'function' ? cleanup : null; });
        },
    };
    const render = (next = {}) => {
        Object.assign(props, next);
        index = 0;
        const result = view.default(props);
        const effects = pending; pending = [];
        effects.forEach((effect) => effect());
        return result;
    };
    return { render, props, unmount: () => slots.forEach((slot) => slot?.cleanup?.()) };
}

const CH = 'channel';
const keyOf = (chatId) => JSON.stringify(['op', CH, chatId]);
const flush = async () => { for (let turn = 0; turn < 30; turn += 1) await Promise.resolve(); };
const server = (messageId, dt, extra = {}) => ({ messageId, dt, isEcho: false, type: 'text', text: messageId, ...extra });
let calls;
const setup = () => {
    view.resetSendQueueForTests();
    calls = [];
    globalThis.__outboxPost = (url, body) => new Promise((resolve, reject) => calls.push({ body, resolve, reject }));
    globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {}, key: () => null, length: 0 };
    view.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
};
const send = (chatId, text, extra = {}) => view.enqueueMessage({ clientMessageId: `id-${text}`, channelId: CH, chatId,
    text, displayText: text, authorName: 'Оператор', ...extra });

test('a sent message is in the thread at once, at the end, and gives way to its archive row', async () => {
    setup();
    const thread = [server('old', '2026-10-09T10:00:00Z')];
    const v = mount({ thread, selectedKey: keyOf('A') });
    send('A', 'Привет');
    let out = v.render();
    assert.deepEqual(out.threadWithDays.filter((m) => !m._day).map((m) => [m.messageId, m.status ?? null]),
        [['old', null], ['local:id-Привет', 'queued']]);
    await flush();
    calls[0].resolve({ data: { state: 'sent', messageId: 'm1' } });
    await flush();
    out = v.render();
    assert.equal(out.localMessages[0].status, 'pending');
    v.render({ thread: [...thread, { ...server('m1', '2026-10-09T10:01:00Z'), isEcho: true, clientMessageId: 'id-Привет' }] });
    out = v.render();
    assert.deepEqual(out.threadWithDays.filter((m) => !m._day).map((m) => m.messageId), ['old', 'm1']);
    assert.equal(view.outboxSnapshot().length, 0, 'the archive row settled the outbox');
    v.unmount();
});

test('a failure stays on its own time and day; a message on the way stays last; day keys are unique', async (t) => {
    // The bubble on the way is stamped "now": pin the clock, or the test depends on the run date.
    mock.timers.enable({ apis: ['Date'], now: Date.parse('2026-10-09T11:00:00Z') });
    t.after(() => mock.timers.reset());
    setup();
    const v = mount({ selectedKey: keyOf('A'), thread: [server('yesterday', '2026-10-08T08:00:00Z'),
        server('today', '2026-10-09T10:00:00Z')] });
    send('A', 'отказ');
    await flush();
    calls[0].reject({ response: { status: 422, data: { state: 'failed', error: 'Окно закрыто' } } });
    await flush();
    // Make the failure yesterday's, as after a night in the same tab.
    const failed = view.outboxSnapshot()[0];
    view.resetSendQueueForTests([{ ...failed, createdAt: '2026-10-08T09:00:00Z' }]);
    view.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    send('A', 'в пути');
    const out = v.render();
    assert.deepEqual(out.threadWithDays.map((m) => m.messageId), ['day-2026-10-08', 'yesterday', 'local:id-отказ',
        'day-2026-10-09', 'today', 'local:id-в пути']);
    assert.equal(new Set(out.threadWithDays.map((m) => m.messageId)).size, out.threadWithDays.length);
    v.unmount();
});

test('an accepted message without its archive row is re-read: soon without the live stream, later with it, per message', async () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    try {
        setup();
        let reads = 0;
        let lists = 0;
        const v = mount({ selectedKey: keyOf('A'), thread: [server('old', '2026-10-09T10:00:00Z')],
            pilot: { connection: 'reconnecting' },
            refreshPilotThread: async () => { reads += 1; }, loadChats: () => { lists += 1; return Promise.resolve([]); } });
        send('A', 'первое');
        v.render(); await flush();
        calls[0].resolve({ data: { state: 'sent', messageId: 'm1' } });
        await flush(); v.render();
        mock.timers.tick(599); assert.equal(reads, 0);
        mock.timers.tick(1); assert.deepEqual([reads, lists], [1, 1]);
        send('A', 'второе');
        v.render(); await flush();
        calls[1].resolve({ data: { state: 'sent', messageId: 'm2' } });
        await flush(); v.render();
        mock.timers.tick(600);
        assert.equal(reads, 2, 'the second accepted message re-arms the check');
        v.render({ pilot: { connection: 'live' } });
        const before = reads;
        mock.timers.tick(3999); assert.equal(reads, before);
        mock.timers.tick(1); assert.equal(reads, before + 1);
        v.unmount();
    } finally {
        mock.timers.reset();
    }
});

test('a send failing in another chat is announced once; the open chat and earlier failures are not', async () => {
    setup();
    const toasts = [];
    send('B', 'старый');
    await flush();
    calls[0].reject({ response: { status: 422, data: { state: 'failed', error: 'Отказ' } } });
    await flush();
    const v = mount({ selectedKey: keyOf('A'), thread: [], showToast: (text, kind) => toasts.push([text, kind]),
        chats: [{ channelId: CH, chatId: 'C', contactName: 'Алия' }] });
    v.render();
    assert.deepEqual(toasts, [], 'a failure from before the section opened is only marked in the list');
    send('C', 'новый');
    send('A', 'здесь');
    await flush();
    calls[1].reject({ response: { status: 422, data: { state: 'failed', error: 'Отказ' } } });
    calls[2].reject({ response: { status: 422, data: { state: 'failed', error: 'Отказ' } } });
    await flush();
    let out = v.render();
    out = v.render();
    assert.deepEqual(toasts, [['Сообщение для Алия не отправлено — откройте этот чат', 'error']]);
    assert.deepEqual([...out.problemChats].sort(), [keyOf('A'), keyOf('B'), keyOf('C')].sort());
    // Later queue changes (another send) must not repeat the notice.
    send('D', 'ещё одно');
    v.render();
    await flush();
    calls[3].resolve({ data: { state: 'sent', messageId: 'm4' } });
    await flush();
    v.render();
    assert.equal(toasts.length, 1);
    v.unmount();
});

test('«Изменить» takes the rejected text back into this chat\'s field', async () => {
    setup();
    const v = mount({ selectedKey: keyOf('A'), thread: [] });
    send('A', 'исправлю');
    await flush();
    calls[0].reject({ response: { status: 422, data: { state: 'failed', error: 'Отказ' } } });
    await flush();
    let out = v.render();
    assert.equal(out.localMessages[0].local.editable, true);
    out.editFailed('id-исправлю');
    out = v.render();
    assert.deepEqual(out.draftRestore, { key: keyOf('A'), id: 'id-исправлю', text: 'исправлю' });
    assert.equal(view.outboxSnapshot().length, 0);
    assert.equal(out.localMessages.length, 0);
    v.unmount();
});
