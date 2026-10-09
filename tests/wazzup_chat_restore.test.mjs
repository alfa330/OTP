import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

// Execute the real view's loaders, navigation handlers, cleanup and deep-link
// effect. HTTP promises deliberately ignore abort so late replies exercise the
// component guards rather than relying on a cooperative network mock.
const source = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8').replace(/\r\n/g, '\n');
const between = (start, end) => {
    const first = source.indexOf(start), last = source.indexOf(end, first);
    assert.ok(first >= 0 && last > first, `view fragment: ${start}`);
    return source.slice(first, last);
};
const cleanup = between('    const searchDebounce = useRef(null);', '    // Личные черновики');
const loaders = between('    const loadChats = ', '    const refreshPilotThread = ');
const navigation = between('    const switchAccount = ', '    /* Открытый чат живёт в адресной строке:');
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzup-chat-restore.mjs');
await build({ stdin: { contents: `
    import { findWazzupChatExact, matchWazzupChatsByPhone, normalizeWazzupAccount } from './chatLink';
    import { mergePilotMessages, pilotChatKey } from './chatPilot';
    import { mergeChatRow } from './chatListOrder';
    const h=()=>globalThis.__chatRestore;
    const useState=(...args)=>h().useState(...args), useRef=(...args)=>h().useRef(...args), useEffect=(...args)=>h().useEffect(...args);
    const axios={get:(...args)=>h().get(...args),isCancel:(error)=>error?.name==='CanceledError'};
    const latestInboundTime=()=>null, requestAnimationFrame=()=>{}, PAGE_SIZE=30, THREAD_PAGE=50;
    const wazzupChatUrl=(target,accounts,account)=>account+':'+target.channelId+'/'+target.chatId;
    export default function Restore({initialChat, operator=false, apiBaseUrl='/synthetic'}) {
        const [account,setAccount]=useState(operator?'op':normalizeWazzupAccount(initialChat?.account));
        const accountRef=useRef(account); accountRef.current=account;
        const [channels,setChannels]=useState(null), [chats,setChats]=useState(null), [chatsTotal,setChatsTotal]=useState(0);
        const [chatsError,setChatsError]=useState(null), [search,setSearch]=useState(''), [appliedSearch,setAppliedSearch]=useState('');
        const [selected,setSelected]=useState(null), [thread,setThread]=useState(null), [inboundTime,setInboundTime]=useState(null);
        const [threadHasMore,setThreadHasMore]=useState(false), [threadLoadingMore,setThreadLoadingMore]=useState(false);
        const [deepLinkResolving,setDeepLinkResolving]=useState(false), [deepLinkMiss,setDeepLinkMiss]=useState('');
        const [deepLinkMany,setDeepLinkMany]=useState(''), [deepLinkChatUrl,setDeepLinkChatUrl]=useState('');
        const [unreadOnly,setUnreadOnly]=useState(false), [mainTab,setMainTab]=useState('chats');
        const initialChatDone=useRef(''), deepLinkRequest=useRef(null), deepLinkPending=useRef(false);
        const chatsRequest=useRef({id:0,controller:null}), threadRequest=useRef({id:0,controller:null});
        const pilotRefresh=useRef({id:0,controller:null}), liveChatSummaries=useRef({seq:0,items:new Map()}), threadBox=useRef(null);
        const accounts=[], headers=()=>({}), showToast=(...args)=>h().toasts.push(args);
        const loadChannels=()=>h().channels.push(accountRef.current);
        const onInitialChatConsumed=()=>h().consume();
        ${cleanup}
        ${loaders}
        ${navigation}
        return {account, search, appliedSearch, selected, thread, chats, chatsTotal, deepLinkResolving, deepLinkMiss,
            deepLinkMany, deepLinkChatUrl, pending:deepLinkPending.current, loadChats, openChat, onSearchInput, switchAccount,
            lookup:deepLinkRequest.current};
    }
`, resolveDir: join(process.cwd(), 'src/components/wazzup'), loader: 'jsx' }, outfile: output,
    bundle: true, format: 'esm', platform: 'node' });
const { default: Restore } = await import(pathToFileURL(output));
const flush = async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); };
const chat = (chatId, channelId = 'channel-a', extra = {}) => ({ channelId, chatId, chatType: 'whatsapp',
    contactName: `Контакт ${chatId}`, lastMessageAt: '2026-10-12T10:00:00Z', ...extra });
const a = chat('70000000001'), b = chat('70000000002', 'channel-b');
const pageRows = Array.from({ length: 30 }, (_, i) => chat(`7100000${String(i).padStart(4, '0')}`));

function fixture(initialChat, { autoConsume = false, operator = false } = {}) {
    const slots = [], requests = [];
    let cursor = 0, effects = [], dirty = false, unmounted = false, lateWrites = 0;
    const props = { initialChat, operator };
    const h = {
        requests, toasts: [], channels: [], consumed: 0,
        get(url, config) {
            let resolve, reject;
            const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
            requests.push({ url, ...config, resolve: (data) => resolve({ data }), reject });
            return promise;
        },
        consume() { h.consumed += 1; if (autoConsume) { props.initialChat = null; dirty = true; } },
        useState(initial) { const index = cursor++; slots[index] ??= { value: typeof initial === 'function' ? initial() : initial };
            return [slots[index].value, (input) => { if (unmounted) lateWrites += 1;
                const previous = slots[index].value, next = typeof input === 'function' ? input(previous) : input;
                slots[index].value = next; dirty ||= !Object.is(previous, next);
            }]; },
        useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
        useEffect(effect, deps) { const index = cursor++, previous = slots[index];
            if (previous && deps.every((value, i) => Object.is(value, previous.deps[i]))) return;
            slots[index] = { deps, effect }; effects.push(() => { previous?.cleanup?.(); slots[index].cleanup = effect(); }); },
        render(next = {}) {
            Object.assign(props, next); globalThis.__chatRestore = h;
            let out, rounds = 0;
            do {
                assert.ok(rounds++ < 12, 'effects settle'); cursor = 0; dirty = false;
                out = Restore(props); const pending = effects; effects = []; pending.forEach((effect) => effect());
            } while (dirty);
            return out;
        },
        list() { return requests.filter((request) => request.url.endsWith('/chats') && !request.params.chat_id).at(-1); },
        lookup() { return requests.filter((request) => request.url.endsWith('/chats') && request.params.chat_id).at(-1); },
        thread() { return requests.filter((request) => request.url.endsWith('/chat-messages')).at(-1); },
        async firstPage(rows = pageRows, total = 90) {
            h.list().resolve({ items: rows, total }); await flush(); return h.render();
        },
        unmount() { if (unmounted) return; unmounted = true; slots.forEach((slot) => slot?.cleanup?.()); },
        restore() { h.unmount(); delete globalThis.__chatRestore; },
        get lateWrites() { return lateWrites; },
    };
    return h;
}

test('exact restoration loads the full list with empty search and reuses first-page metadata', async () => {
    const h = fixture(a, { autoConsume: true });
    try {
        let out = h.render();
        assert.equal(out.search, ''); assert.equal(out.appliedSearch, '');
        assert.equal(h.list().params.q, undefined);
        assert.equal(h.list().params.channel_id, undefined);
        assert.deepEqual([h.thread().params.channel_id, h.thread().params.chat_id], [a.channelId, a.chatId]);
        assert.equal(h.consumed, 1);
        assert.equal(out.lookup.signal.aborted, false, 'consuming the initial prop must not cancel restoration');
        const rows = [a, ...pageRows];
        out = await h.firstPage(rows, 91);
        assert.equal(out.selected, a);
        assert.deepEqual(out.chats, rows);
        assert.equal(out.chatsTotal, 91);
        assert.equal(h.lookup(), undefined, 'first-page metadata needs no additional request');
        assert.equal(out.pending, false);
    } finally { h.restore(); }
});

test('a chat outside the first page uses an exact lookup without changing the list or pagination', async () => {
    const h = fixture(a);
    try {
        h.render(); let out = await h.firstPage();
        assert.deepEqual(h.lookup().params, { account: 'op', channel_id: a.channelId, chat_id: a.chatId, limit: 1 });
        h.lookup().resolve({ items: [a], total: 1 }); await flush(); out = h.render();
        assert.equal(out.selected, a);
        assert.deepEqual(out.chats, pageRows);
        assert.equal(out.chatsTotal, 90);
        out.loadChats({ reset: false });
        assert.equal(h.list().params.offset, 30, 'selected metadata is never inserted into the page');
        assert.equal(h.list().params.q, undefined);
        assert.equal(out.search, '');
    } finally { h.restore(); }
});

test('late exact lookup replies cannot replace or close a manually chosen chat', async () => {
    for (const reply of [{ items: [a] }, { items: [] }, new Error('late network error')]) {
        const h = fixture(a);
        try {
            h.render(); let out = await h.firstPage(); const old = h.lookup();
            out.openChat(b); out = h.render();
            assert.equal(old.signal.aborted, true);
            h.thread().resolve({ items: [{ messageId: 'new-chat-message' }], hasMore: false }); await flush();
            if (reply instanceof Error) old.reject(reply); else old.resolve(reply);
            await flush(); out = h.render();
            assert.equal(out.selected, b);
            assert.equal(out.thread[0].messageId, 'new-chat-message');
            assert.equal(out.deepLinkMiss, '');
            assert.deepEqual(h.toasts, []);
        } finally { h.restore(); }
    }
});

test('manual search and account changes invalidate a pending exact lookup', async () => {
    for (const action of ['search', 'account']) {
        const h = fixture(a);
        try {
            h.render(); let out = await h.firstPage(); const old = h.lookup();
            if (action === 'search') out.onSearchInput('Новый поиск'); else out.switchAccount('potok');
            out = h.render(); assert.equal(old.signal.aborted, true);
            old.resolve({ items: [] }); await flush(); out = h.render();
            assert.equal(out.deepLinkMiss, ''); assert.equal(out.deepLinkChatUrl, '');
            if (action === 'search') {
                assert.equal(out.search, 'Новый поиск');
                assert.equal(out.selected.chatId, a.chatId);
            } else {
                assert.equal(out.account, 'potok'); assert.equal(out.selected, null);
                assert.equal(h.list().params.account, 'potok');
                assert.deepEqual(h.channels, ['potok']);
            }
        } finally { h.restore(); }
    }
});

test('lookup failure keeps the existing conversation and never reports it as missing', async () => {
    for (const error of [new Error('offline'), { response: { status: 403 } }, { response: { status: 500 } }]) {
        const h = fixture(a);
        try {
            h.render(); await h.firstPage();
            h.thread().resolve({ items: [{ messageId: 'loaded-history' }], hasMore: false }); await flush();
            h.lookup().reject(error); await flush(); const out = h.render();
            assert.equal(out.selected.chatId, a.chatId);
            assert.equal(out.thread[0].messageId, 'loaded-history');
            assert.equal(out.deepLinkMiss, ''); assert.equal(out.pending, false);
            assert.equal(h.toasts.length, 1);
        } finally { h.restore(); }
    }
});

test('a confirmed missing exact chat cancels its history and ignores a late history response', async () => {
    const h = fixture(a);
    try {
        h.render(); const history = h.thread(); await h.firstPage();
        h.lookup().resolve({ items: [] }); await flush(); let out = h.render();
        assert.equal(history.signal.aborted, true);
        assert.equal(out.selected, null); assert.equal(out.thread, null);
        assert.equal(out.deepLinkMiss, a.chatId); assert.equal(out.pending, true);
        history.resolve({ items: [{ messageId: 'late-history' }] }); await flush(); out = h.render();
        assert.equal(out.thread, null);
    } finally { h.restore(); }
});

test('phone-only links keep explicit number search and ambiguity instead of picking another channel', async () => {
    for (const rows of [[a], [a, { ...a, channelId: 'other-channel' }], []]) {
        const h = fixture({ phone: a.chatId });
        try {
            let out = h.render();
            assert.equal(out.search, a.chatId); assert.equal(out.appliedSearch, a.chatId);
            assert.equal(out.deepLinkResolving, true); assert.equal(h.thread(), undefined);
            assert.equal(h.list().params.q, a.chatId);
            out = await h.firstPage(rows, rows.length);
            assert.equal(h.lookup(), undefined);
            if (rows.length === 1) assert.equal(out.selected, a);
            else if (rows.length > 1) { assert.equal(out.selected, null); assert.equal(out.deepLinkMany, a.chatId); }
            else assert.equal(out.deepLinkMiss, a.chatId);
            assert.equal(out.deepLinkResolving, false);
        } finally { h.restore(); }
    }
});

test('manual search or account change clears the pending phone-link loading screen', async () => {
    for (const action of ['search', 'account']) {
        const h = fixture({ phone: a.chatId });
        try {
            let out = h.render(); const old = h.list();
            assert.equal(out.deepLinkResolving, true);
            if (action === 'search') out.onSearchInput('Другой контакт'); else out.switchAccount('potok');
            old.resolve({ items: [a], total: 1 }); await flush(); out = h.render();
            assert.equal(out.selected, null);
            assert.equal(out.deepLinkResolving, false, 'cancelled navigation must not leave a permanent loading screen');
        } finally { h.restore(); }
    }
});

test('a newer exact target supersedes an older lookup, and unmount aborts pending resolution', async () => {
    const h = fixture(a);
    try {
        h.render(); await h.firstPage(); const old = h.lookup();
        h.render({ initialChat: b }); assert.equal(old.signal.aborted, true);
        await h.firstPage([b]);
        old.resolve({ items: [] }); await flush();
        assert.equal(h.render().selected, b);
        h.render({ initialChat: { ...a, account: 'potok' } }); await h.firstPage();
        const pending = h.lookup(); h.unmount();
        assert.equal(pending.signal.aborted, true);
        pending.resolve({ items: [a] }); await flush();
        assert.equal(h.lateWrites, 0);
    } finally { h.restore(); }
});
