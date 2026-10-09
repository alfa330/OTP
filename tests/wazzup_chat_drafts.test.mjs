import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

// Personal drafts of «Чаты ОП» (chatDrafts.js): visible only to their author,
// kept in this browser's localStorage under the person's id. Each scenario loads
// a fresh module instance, as a page load does. No network.
const require = createRequire(import.meta.url);
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzupChatDrafts.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/chatDrafts.js')],
    outfile: output, bundle: true, format: 'esm', platform: 'node', target: 'node18',
    plugins: [{ name: 'react-double', setup(builder) {
        builder.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'react-double' }));
        builder.onLoad({ filter: /.*/, namespace: 'react-double' }, () => ({ loader: 'js',
            contents: 'export const useSyncExternalStore=(subscribe,get)=>get();' }));
    } }],
});

const memory = (entries = []) => {
    const map = new Map(entries);
    return { map, getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, String(v)),
        removeItem: (k) => map.delete(k), key: (i) => [...map.keys()][i] ?? null, get length() { return map.size; } };
};
let tab = 0;
const freshTab = async (storage) => {
    globalThis.localStorage = storage;
    const listeners = new Map();
    globalThis.addEventListener = (type, listener) => listeners.set(type, listener);
    tab += 1;
    const module = await import(`${pathToFileURL(output).href}?tab=${tab}`);
    return { ...module, fireStorage: (key) => listeners.get('storage')?.({ key }) };
};
const CHAT = JSON.stringify(['op', 'channel', '77000000001']);
const OTHER = JSON.stringify(['op', 'channel', '77000000002']);

test('a draft belongs to its author: another person in the same browser never sees it', async () => {
    const storage = memory();
    const drafts = await freshTab(storage);
    drafts.writeChatDraft(7, CHAT, { text: 'Мой ответ' });
    assert.equal(drafts.readChatDraft(7, CHAT).text, 'Мой ответ');
    assert.equal(drafts.readChatDraft(8, CHAT), null);
    assert.deepEqual(Object.keys(drafts.useChatDrafts(8)), []);
    assert.deepEqual(Object.keys(drafts.useChatDrafts(7)), [CHAT]);
    assert.ok(storage.map.has(drafts.chatDraftStorageKey(7, CHAT)));
    assert.equal(drafts.readChatDraft(null, CHAT), null, 'no person — no drafts');
});

test('drafts survive a reload and a new tab of the same person', async () => {
    const storage = memory();
    (await freshTab(storage)).writeChatDraft(7, CHAT, { text: 'Не успел отправить', preview: '' });
    const reloaded = await freshTab(storage);
    assert.equal(reloaded.readChatDraft(7, CHAT).text, 'Не успел отправить');
});

test('an emptied field removes the draft; a week-old draft is dropped and cleaned up', async () => {
    const storage = memory();
    const drafts = await freshTab(storage);
    drafts.writeChatDraft(7, CHAT, { text: 'Удалю' });
    drafts.writeChatDraft(7, CHAT, { text: '', preview: '', attachment: null });
    assert.equal(drafts.readChatDraft(7, CHAT), null);
    assert.equal(storage.map.size, 0);
    const old = new Date(Date.now() - drafts.DRAFT_MAX_AGE_MS - 60_000).toISOString();
    storage.setItem(drafts.chatDraftStorageKey(7, OTHER), JSON.stringify({ text: 'давний', updatedAt: old }));
    storage.setItem(drafts.chatDraftStorageKey(7, CHAT), JSON.stringify({ text: 'свежий', updatedAt: new Date().toISOString() }));
    const next = await freshTab(storage);
    assert.deepEqual(Object.keys(next.useChatDrafts(7)), [CHAT]);
    assert.equal(storage.map.has(drafts.chatDraftStorageKey(7, OTHER)), false);
});

test('typing does not re-render the section; leaving the chat does; another tab of the person updates the list', async () => {
    const storage = memory();
    const drafts = await freshTab(storage);
    let renders = 0;
    drafts.subscribeChatDrafts(() => { renders += 1; });
    drafts.writeChatDraft(7, CHAT, { text: 'п' }, { notify: false });
    drafts.writeChatDraft(7, CHAT, { text: 'пр' }, { notify: false });
    assert.equal(renders, 0);
    assert.equal(drafts.useChatDrafts(7)[CHAT].text, 'пр', 'the snapshot is current even without a render');
    drafts.notifyChatDrafts();
    assert.equal(renders, 1);
    // The same person typed in another tab of this browser.
    storage.setItem(drafts.chatDraftStorageKey(7, OTHER), JSON.stringify({ text: 'из другой вкладки', updatedAt: new Date().toISOString() }));
    drafts.fireStorage(drafts.chatDraftStorageKey(7, OTHER));
    assert.equal(renders, 2);
    assert.equal(drafts.useChatDrafts(7)[OTHER].text, 'из другой вкладки');
    drafts.fireStorage(drafts.chatDraftStorageKey(8, OTHER));
    assert.equal(renders, 2, "another person's key is not this list's business");
});

test('the chat list shows a draft only for chats that are not open, as text, template or file name', async () => {
    const drafts = await freshTab(memory());
    const map = { [CHAT]: { text: '[[code]]', preview: 'Здравствуйте, Алия!' }, [OTHER]: { text: '', attachment: { name: 'скан.pdf' } } };
    assert.equal(drafts.chatListDraft(map, CHAT, { owner: 7, open: false }), 'Здравствуйте, Алия!');
    assert.equal(drafts.chatListDraft(map, OTHER, { owner: 7, open: false }), 'скан.pdf');
    assert.equal(drafts.chatListDraft(map, CHAT, { owner: 7, open: true }), '');
    assert.equal(drafts.chatListDraft(map, CHAT, { owner: null, open: false }), '');
    assert.equal(drafts.chatListDraft({}, CHAT, { owner: 7, open: false }), '');
});
