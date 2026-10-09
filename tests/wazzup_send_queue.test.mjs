import test, { mock } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { outboxMessages, pilotDraftStorageKey } from '../src/components/wazzup/chatPilot.js';

// The real send queue, bundled with an in-test HTTP double and a virtual clock.
// No network, browser or credentials: nothing here can reach Wazzup.
const require = createRequire(import.meta.url);
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzupSendQueue.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/sendQueue.js')],
    outfile: output, bundle: true, format: 'esm', platform: 'node', target: 'node18',
    plugins: [{ name: 'queue-doubles', setup(build) {
        build.onResolve({ filter: /^(react|axios)$/ }, ({ path }) => ({ path, namespace: 'queue-double' }));
        build.onLoad({ filter: /.*/, namespace: 'queue-double' }, ({ path }) => ({ loader: 'js', contents: path === 'react'
            ? 'export const useMemo=(fn)=>fn(); export const useSyncExternalStore=(subscribe,get)=>get();'
            : 'export default {post:(...args)=>globalThis.__wazzupQueuePost(...args)};' }));
    } }],
});

let instances = 0;
// Every scenario gets a fresh module instance: the queue is a per-tab singleton
// that reads sessionStorage when the bundle loads, exactly like a page load.
const freshQueue = async (storage) => {
    globalThis.sessionStorage = storage;
    instances += 1;
    return import(`${pathToFileURL(output).href}?tab=${instances}`);
};

const memoryStorage = (entries = []) => {
    const map = new Map(entries);
    return {
        map, getItem: (key) => (map.has(key) ? map.get(key) : null), setItem: (key, value) => map.set(key, String(value)),
        removeItem: (key) => map.delete(key), key: (index) => [...map.keys()][index] ?? null, get length() { return map.size; },
    };
};

const controlledPost = () => {
    const calls = [];
    globalThis.__wazzupQueuePost = (url, body, config) => new Promise((resolve, reject) => {
        calls.push({ url, body, config, resolve, reject });
    });
    return calls;
};
const accepted = (messageId) => ({ data: { status: 'success', state: 'sent', messageId } });
const flush = async () => { for (let turn = 0; turn < 30; turn += 1) await Promise.resolve(); };
const states = (q) => Object.fromEntries(q.outboxSnapshot().map((item) => [item.text, item.state]));
const message = (text, extra = {}) => ({ clientMessageId: `id-${text}`, channelId: 'channel', chatId: 'A',
    text, displayText: text, authorName: 'Оператор', ...extra });

test('one chat sends in typing order, one request at a time; another chat is not held up', async () => {
    const q = await freshQueue(memoryStorage());
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '/api-base', ownerId: 7, headers: () => ({ Authorization: 'Bearer token' }) });
    for (const [text, chatId] of [['первое', 'A'], ['второе', 'A'], ['другой чат', 'B'], ['третье', 'A']]) {
        q.enqueueMessage(message(text, { chatId }));
    }
    await flush();
    assert.deepEqual(calls.map((call) => call.body.text), ['первое', 'другой чат']);
    assert.equal(calls[0].url, '/api-base/api/wazzup/pilot/send');
    assert.deepEqual(calls[0].config.headers, { Authorization: 'Bearer token' });
    assert.deepEqual(calls[0].body, { account: 'op', channelId: 'channel', chatId: 'A', text: 'первое', clientMessageId: 'id-первое' });
    calls[0].resolve(accepted('vendor-1'));
    await flush();
    assert.equal(calls[2].body.text, 'второе');
    calls[2].reject({ response: { status: 422, data: { state: 'failed', error: 'Окно WABA закрыто' } } });
    await flush();
    assert.equal(calls[3].body.text, 'третье', 'a rejected message does not hold up the next one');
    assert.deepEqual(states(q), { 'первое': 'sent', 'второе': 'failed', 'другой чат': 'sending', 'третье': 'sending' });
    assert.equal(q.outboxSnapshot().find((item) => item.text === 'второе').error, 'Окно WABA закрыто');
});

test('a lost response or a restarting API is re-checked with the same id and payload', async () => {
    mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_000_000 });
    try {
        const q = await freshQueue(memoryStorage());
        const calls = controlledPost();
        q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
        q.enqueueMessage(message('Привет', { replyToMessageId: 'quoted', attachmentId: undefined }));
        await flush();
        calls[0].reject({ code: 'ECONNABORTED' });
        await flush();
        assert.equal(q.outboxSnapshot()[0].state, 'checking');
        assert.equal(calls.length, 1, 'no immediate second request');
        mock.timers.tick(1000); await flush();
        assert.equal(calls.length, 2);
        assert.deepEqual(calls[1].body, calls[0].body);
        assert.equal(calls[1].body.replyToMessageId, 'quoted');
        calls[1].reject({ response: { status: 502 } });
        await flush(); mock.timers.tick(2000); await flush();
        assert.deepEqual(calls[2].body, calls[0].body);
        calls[2].resolve(accepted('vendor-9'));
        await flush();
        const [item] = q.outboxSnapshot();
        assert.deepEqual([item.state, item.messageId], ['sent', 'vendor-9']);
    } finally {
        mock.timers.reset();
    }
});

test('rechecks are bounded: an answerless send becomes unconfirmed, and "Проверить" asks with the same id', async () => {
    mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 0 });
    try {
        const q = await freshQueue(memoryStorage());
        const calls = controlledPost();
        q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
        q.enqueueMessage(message('Без ответа'));
        for (const delay of [0, ...q.RECHECK_DELAYS_MS]) {
            mock.timers.tick(delay); await flush();
            calls.at(-1).reject({ code: 'ERR_NETWORK' });
            await flush();
        }
        assert.equal(calls.length, q.RECHECK_DELAYS_MS.length + 1);
        assert.equal(q.outboxSnapshot()[0].state, 'unknown');
        mock.timers.tick(60000); await flush();
        assert.equal(calls.length, q.RECHECK_DELAYS_MS.length + 1, 'no endless retries');
        q.retryMessage('id-Без ответа');
        await flush();
        assert.deepEqual(calls.at(-1).body, calls[0].body);
        calls.at(-1).resolve(accepted('found'));
        await flush();
        assert.equal(q.outboxSnapshot()[0].state, 'sent');
    } finally {
        mock.timers.reset();
    }
});

test('an attempt still talking to Wazzup is polled and finally left unconfirmed, never sent under a new id', async () => {
    mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 0 });
    try {
        const q = await freshQueue(memoryStorage());
        const calls = controlledPost();
        q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
        q.enqueueMessage(message('В пути'));
        const busy = { response: { status: 409, data: { state: 'sending', code: 'SEND_IN_PROGRESS',
            error: 'Отправка уже начата. Проверьте появление сообщения в чате.' } } };
        await flush();
        calls[0].reject(busy);
        await flush();
        assert.equal(q.outboxSnapshot()[0].state, 'checking');
        let answered = 1;
        while (Date.now() < q.IN_PROGRESS_LIMIT_MS + 5000 && q.outboxSnapshot()[0].state === 'checking') {
            mock.timers.tick(1500); await flush();
            for (; answered < calls.length; answered += 1) { calls[answered].reject(busy); await flush(); }
        }
        assert.equal(q.outboxSnapshot()[0].state, 'unknown');
        assert.match(q.outboxSnapshot()[0].error, /Отправка уже начата/);
        assert.ok(calls.length > 2);
        assert.ok(calls.every((call) => call.body.clientMessageId === 'id-В пути'));
    } finally {
        mock.timers.reset();
    }
});

test('"Повторить" after a rejection is a new message with a new id; "Убрать" only removes the bubble', async () => {
    const q = await freshQueue(memoryStorage());
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    q.enqueueMessage(message('Отказ', { replyToMessageId: 'quoted' }));
    await flush();
    calls[0].reject({ response: { status: 422, data: { state: 'failed', error: 'Канал отключён' } } });
    await flush();
    q.retryMessage('id-Отказ');
    await flush();
    assert.equal(calls.length, 2);
    assert.notEqual(calls[1].body.clientMessageId, calls[0].body.clientMessageId);
    assert.deepEqual({ ...calls[1].body, clientMessageId: 'x' }, { ...calls[0].body, clientMessageId: 'x' });
    assert.equal(q.outboxSnapshot().length, 1, 'the new attempt replaces the failed bubble');
    calls[1].reject({ response: { status: 422, data: { state: 'failed', error: 'Канал отключён' } } });
    await flush();
    const failedId = q.outboxSnapshot()[0].clientMessageId;
    q.retryMessage('no-such-id');
    q.discardMessage(failedId);
    await flush();
    assert.equal(q.outboxSnapshot().length, 0);
    assert.equal(calls.length, 2, 'discarding sends nothing');
});

test('another person in the tab: the previous owner\'s queue is dropped and never sent with new credentials', async () => {
    const storage = memoryStorage();
    const q = await freshQueue(storage);
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({ Authorization: 'first' }) });
    q.enqueueMessage(message('первое'));
    q.enqueueMessage(message('ждёт очереди'));
    await flush();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 8, headers: () => ({ Authorization: 'second' }) });
    calls[0].resolve(accepted('late'));
    await flush();
    assert.equal(calls.length, 1);
    assert.deepEqual(calls.map((call) => call.config.headers.Authorization), ['first']);
    assert.equal(q.outboxSnapshot().length, 0);
    assert.equal(storage.map.size, 0);
    assert.equal(q.enqueueMessage(message('новое')).ownerId, 8);
    await flush();
    assert.deepEqual(calls.at(-1).config.headers, { Authorization: 'second' });
});

test('a reload re-sends unfinished messages with their own ids; accepted ones are not stored', async () => {
    const storage = memoryStorage();
    const first = await freshQueue(storage);
    const calls = controlledPost();
    first.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    first.enqueueMessage(message('принято'));
    first.enqueueMessage(message('оборвано', { attachmentId: '8f49c2ce-349f-4e91-92dc-a32b0c4f04b5',
        attachment: { name: 'file.pdf', size: 10, mime: 'application/pdf' }, text: '', displayText: 'file.pdf' }));
    await flush();
    calls[0].resolve(accepted('vendor-1'));
    await flush();
    assert.equal(calls.length, 2, 'the second message is in flight when the page reloads');
    const saved = JSON.parse(storage.map.get('icore.wazzup.pilot.outbox.v1'));
    assert.deepEqual(saved.map((item) => [item.clientMessageId, item.state]), [['id-оборвано', 'sending']]);

    const reloaded = await freshQueue(storage);
    const again = controlledPost();
    reloaded.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    await flush();
    assert.equal(again.length, 1);
    assert.deepEqual(again[0].body, calls[1].body, 'same id, same attachment, no second upload');
});

test('an uncertain send of the previous composer is adopted with its own id and its text leaves the draft', async () => {
    const chat = { channelId: 'channel', chatId: 'A' };
    const textChat = { channelId: 'channel', chatId: 'T' };
    const storage = memoryStorage([
        [pilotDraftStorageKey(textChat), JSON.stringify({ text: 'Старый текст', preview: 'Шаблон для глаз',
            replyTo: { messageId: 'q', text: 'Вопрос', authorName: 'Клиент' },
            pending: { account: 'op', ...textChat, text: '[[code]]', clientMessageId: 'legacy-text', replyToMessageId: 'q' } })],
        [pilotDraftStorageKey(chat), JSON.stringify({ text: 'Черновик рядом с файлом',
            attachment: { id: '8f49c2ce-349f-4e91-92dc-a32b0c4f04b5', name: 'scan.pdf', size: 5, mime: 'application/pdf' },
            pending: { account: 'op', ...chat, text: '', attachmentId: '8f49c2ce-349f-4e91-92dc-a32b0c4f04b5',
                clientMessageId: 'legacy-file' } })],
        ['unrelated', 'keep'],
    ]);
    const q = await freshQueue(storage);
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}), authorName: 'Оператор' });
    await flush();
    assert.deepEqual(calls.map((call) => call.body).sort((a, b) => a.chatId.localeCompare(b.chatId)), [
        { account: 'op', channelId: 'channel', chatId: 'A', text: '', clientMessageId: 'legacy-file',
            attachmentId: '8f49c2ce-349f-4e91-92dc-a32b0c4f04b5' },
        { account: 'op', channelId: 'channel', chatId: 'T', text: '[[code]]', clientMessageId: 'legacy-text',
            replyToMessageId: 'q' },
    ]);
    const shown = Object.fromEntries(q.outboxSnapshot().map((item) => [item.clientMessageId, item.displayText]));
    assert.deepEqual(shown, { 'legacy-text': 'Шаблон для глаз', 'legacy-file': 'scan.pdf' });
    assert.equal(storage.map.has(pilotDraftStorageKey(textChat)), false, 'the text is no longer a draft');
    const fileDraft = JSON.parse(storage.map.get(pilotDraftStorageKey(chat)));
    assert.deepEqual([fileDraft.text, fileDraft.attachment, fileDraft.pending], ['Черновик рядом с файлом', null, null]);
    assert.equal(storage.map.get('unrelated'), 'keep');
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    await flush();
    assert.equal(calls.length, 2, 'configuring again adopts nothing twice');
});

test('the archive row replaces the optimistic bubble by message id or by client id, whichever comes first', async () => {
    const q = await freshQueue(memoryStorage());
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    q.enqueueMessage(message('через ответ'));
    q.enqueueMessage(message('через поток', { chatId: 'B' }));
    await flush();
    const thread = [{ messageId: 'old', dt: '2026-10-09T10:00:00Z', isEcho: false, text: 'Вопрос' }];
    let bubbles = outboxMessages(thread, q.outboxSnapshot());
    assert.deepEqual(bubbles.map((bubble) => [bubble.text, bubble.status, bubble.local.state]),
        [['через ответ', 'queued', 'sending'], ['через поток', 'queued', 'sending']]);
    assert.ok(bubbles.every((bubble) => bubble.isEcho && bubble.messageId.startsWith('local:')));
    calls[0].resolve(accepted('vendor-1'));
    await flush();
    bubbles = outboxMessages(thread, q.outboxSnapshot());
    assert.equal(bubbles.find((bubble) => bubble.text === 'через ответ').status, 'pending');
    // SSE delivered the archive row of the second send before its POST returned.
    const withRows = [...thread, { messageId: 'vendor-1', status: 'sent' },
        { messageId: 'vendor-2', clientMessageId: 'id-через поток', status: 'pending' }];
    assert.deepEqual(outboxMessages(withRows, q.outboxSnapshot()), []);
    q.settleOutbox(withRows);
    assert.deepEqual(states(q), { 'через поток': 'sending' }, 'only the accepted one is settled');
    calls[1].resolve(accepted('vendor-2'));
    await flush();
    q.settleOutbox(withRows);
    assert.equal(q.outboxSnapshot().length, 0);
});

test('an accepted message whose archive row never arrives leaves the outbox after two minutes', async () => {
    const q = await freshQueue(memoryStorage());
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    q.enqueueMessage(message('сирота'));
    await flush();
    calls[0].resolve(accepted('vendor-x'));
    await flush();
    const acceptedAt = q.outboxSnapshot()[0].acceptedAt;
    q.settleOutbox([], acceptedAt + 60_000);
    assert.equal(q.outboxSnapshot().length, 1);
    q.settleOutbox([], acceptedAt + 120_001);
    assert.equal(q.outboxSnapshot().length, 0);
});

test('a reload resumes only fresh sends; an old one waits for «Проверить», and day-old failures are dropped', async () => {
    const now = Date.now();
    const ago = (ms) => new Date(now - ms).toISOString();
    const saved = [
        { clientMessageId: 'fresh', account: 'op', channelId: 'channel', chatId: 'A', text: 'свежее', state: 'checking',
            createdAt: ago(30_000), ownerId: 7, apiBaseUrl: '' },
        { clientMessageId: 'old', account: 'op', channelId: 'channel', chatId: 'B', text: 'вчерашнее', state: 'sending',
            createdAt: ago(3 * 60 * 60 * 1000), ownerId: 7, apiBaseUrl: '' },
        { clientMessageId: 'stale-fail', account: 'op', channelId: 'channel', chatId: 'C', text: 'давний отказ', state: 'failed',
            createdAt: ago(25 * 60 * 60 * 1000), ownerId: 7, apiBaseUrl: '' },
        { clientMessageId: 'recent-fail', account: 'op', channelId: 'channel', chatId: 'C', text: 'отказ', state: 'failed',
            createdAt: ago(60_000), ownerId: 7, apiBaseUrl: '' },
    ];
    const q = await freshQueue(memoryStorage([['icore.wazzup.pilot.outbox.v1', JSON.stringify(saved)]]));
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    await flush();
    assert.deepEqual(calls.map((call) => call.body.clientMessageId), ['fresh']);
    assert.deepEqual(states(q), { 'свежее': 'sending', 'вчерашнее': 'unknown', 'отказ': 'failed' });
    assert.match(q.outboxSnapshot().find((item) => item.clientMessageId === 'old').error, /сообщение могло уйти/);
    q.retryMessage('old');
    await flush();
    assert.equal(calls.at(-1).body.clientMessageId, 'old', '«Проверить» asks about the same send');
});

test('«Повторить» goes to the end of the chat queue, where its bubble is drawn', async () => {
    const q = await freshQueue(memoryStorage());
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    q.enqueueMessage(message('первое'));
    q.enqueueMessage(message('второе'));
    q.enqueueMessage(message('третье'));
    await flush();
    calls[0].reject({ response: { status: 422, data: { state: 'failed', error: 'Отказ' } } });
    await flush();
    q.retryMessage('id-первое');          // while «второе» is in flight and «третье» waits
    calls[1].resolve(accepted('m2'));
    await flush();
    calls[2].resolve(accepted('m3'));
    await flush();
    assert.deepEqual(calls.map((call) => call.body.text), ['первое', 'второе', 'третье', 'первое']);
    const drawn = outboxMessages([], q.outboxSnapshot()).map((bubble) => bubble.text);
    assert.deepEqual(drawn, ['второе', 'третье', 'первое']);
});

test('the "still sending" window counts from the first such answer, not from the lost first attempt', async () => {
    mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 0 });
    try {
        const q = await freshQueue(memoryStorage());
        const calls = controlledPost();
        q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
        q.enqueueMessage(message('медленное'));
        await flush();
        mock.timers.tick(31_000);                 // the first attempt hangs, then its answer is lost
        calls[0].reject({ code: 'ERR_NETWORK' });
        await flush(); mock.timers.tick(1000); await flush();
        calls[1].reject({ response: { status: 409, data: { state: 'sending', error: 'Отправка уже начата.' } } });
        await flush();
        assert.equal(q.outboxSnapshot()[0].state, 'checking', 'still polled');
        mock.timers.tick(1500); await flush();
        calls[2].resolve(accepted('m1'));
        await flush();
        assert.equal(q.outboxSnapshot()[0].state, 'sent');
    } finally {
        mock.timers.reset();
    }
});

test('an application answer with an explicit failure is final at once, even as a 503', async () => {
    const q = await freshQueue(memoryStorage());
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    q.enqueueMessage(message('без ключа'));
    await flush();
    calls[0].reject({ response: { status: 503, data: { state: 'failed', code: 'SEND_KEY_MISSING', error: 'Ключ отправки не настроен' } } });
    await flush();
    assert.equal(calls.length, 1, 'no re-checks for a decided answer');
    assert.deepEqual([q.outboxSnapshot()[0].state, q.outboxSnapshot()[0].error], ['failed', 'Ключ отправки не настроен']);
});

test('an unconfirmed send whose archive row arrived leaves the outbox and storage', async () => {
    const storage = memoryStorage();
    const q = await freshQueue(storage);
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    q.enqueueMessage(message('неясное'));
    await flush();
    calls[0].reject({ response: { status: 409, data: { state: 'unknown', error: 'Подтверждение не получено.' } } });
    await flush();
    assert.equal(q.outboxSnapshot()[0].state, 'unknown');
    q.settleOutbox([{ messageId: 'vendor', clientMessageId: 'id-неясное' }]);
    assert.equal(q.outboxSnapshot().length, 0);
    assert.equal(storage.map.has('icore.wazzup.pilot.outbox.v1'), false);
});

test('«Изменить» takes back only a rejected plain text; templates and files stay', async () => {
    const q = await freshQueue(memoryStorage());
    const calls = controlledPost();
    q.configureSendQueue({ apiBaseUrl: '', ownerId: 7, headers: () => ({}) });
    q.enqueueMessage(message('текст'));
    q.enqueueMessage(message('[[code]]', { clientMessageId: 'tpl', displayText: 'Шаблон', chatId: 'B' }));
    q.enqueueMessage(message('', { clientMessageId: 'file', displayText: 'a.pdf', chatId: 'C', attachmentId: 'f',
        attachment: { name: 'a.pdf', size: 1, mime: 'application/pdf' } }));
    await flush();
    for (const call of calls) call.reject({ response: { status: 422, data: { state: 'failed', error: 'Отказ' } } });
    await flush();
    assert.deepEqual(outboxMessages([], q.outboxSnapshot()).map((bubble) => [bubble.text, bubble.local.editable]),
        [['текст', true], ['Шаблон', false], ['a.pdf', false]]);
    assert.equal(q.takeBackMessage('tpl'), null);
    assert.equal(q.takeBackMessage('file'), null);
    assert.equal(q.takeBackMessage('id-текст'), 'текст');
    assert.deepEqual(q.outboxSnapshot().map((item) => item.clientMessageId), ['tpl', 'file']);
    assert.deepEqual(q.outboxProblems(q.outboxSnapshot()).map(q.chatKeyOf),
        [JSON.stringify(['op', 'channel', 'B']), JSON.stringify(['op', 'channel', 'C'])]);
});

test('bubbles: pinned only while on the way, a failure keeps its time, photos are not "documents"', async () => {
    const items = [
        { clientMessageId: 'a', account: 'op', channelId: 'c', chatId: 'x', createdAt: '2026-10-09T10:00:00Z', state: 'sending',
            displayText: 'photo.jpg', attachmentId: 'f1', attachment: { name: 'photo.jpg', mime: 'image/jpeg' } },
        { clientMessageId: 'b', account: 'op', channelId: 'c', chatId: 'x', createdAt: '2026-10-09T09:00:00Z', state: 'failed',
            text: 'нет', displayText: 'нет', error: 'Отказ' },
        { clientMessageId: 'c', account: 'op', channelId: 'c', chatId: 'x', createdAt: '2026-10-09T10:01:00Z', state: 'sent',
            displayText: 'clip.mp4', attachmentId: 'f2', attachment: { name: 'clip.mp4', mime: 'video/mp4' } },
    ];
    const bubbles = outboxMessages([], items);
    assert.deepEqual(bubbles.map((bubble) => [bubble.clientMessageId, bubble.type, bubble.local.pinned, bubble.status]),
        [['b', 'text', false, 'failed'], ['a', 'image', true, 'queued'], ['c', 'video', true, 'pending']]);
});

test('nothing is queued before the queue knows whose it is', async () => {
    const q = await freshQueue(memoryStorage());
    const calls = controlledPost();
    assert.equal(q.enqueueMessage(message('рано')), null);
    q.configureSendQueue({ apiBaseUrl: '', ownerId: null, headers: () => ({}) });
    assert.equal(q.enqueueMessage(message('без человека')), null);
    await flush();
    assert.equal(calls.length, 0);
});
