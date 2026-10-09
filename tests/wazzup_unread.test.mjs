import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pilotChatKey } from '../src/components/wazzup/chatPilot.js';

const source = readFileSync(new URL('../src/components/wazzup/useSharedChatUnread.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replaceAll('export function', 'function').replace('export default function', 'function');
const make = new Function('useEffect','useRef','useState','axios','pilotChatKey','document','window','setTimeout','clearTimeout',
    source + '\nreturn { hook: useSharedChatUnread, mergeUnread, reconcileUnreadSnapshot, presentUnread, holdReply, replySignals, outboxSignals };');
const chat = { channelId: 'channel', chatId: 'chat' };
const key = pilotChatKey('op', chat);
const row = (version, count = 1, id = 'm1') => ({ ...chat, unreadVersion: version, unreadCount: count, lastInboundId: id });
const helpers = make(null,null,null,null,pilotChatKey);

test('a newer snapshot beats delayed SSE, but a newer clear ACK beats a stale snapshot', () => {
    assert.equal(helpers.reconcileUnreadSnapshot([row(3)], { [key]: row(2) }, { [key]: 2 }, 1)[key].unreadVersion, 3);
    assert.equal(helpers.reconcileUnreadSnapshot([row(2)], { [key]: row(3,0) }, { [key]: 2 }, 1)[key].unreadCount, 0);
    assert.deepEqual(helpers.reconcileUnreadSnapshot([], { [key]: row(2) }, { [key]: 1 }, 2), {});
    assert.equal(helpers.mergeUnread({ [key]: row(3,0) }, [row(2)])[key].unreadCount, 0);
});

function harness() {
    const slots = [], timers = new Map(), gets = [], posts = [];
    let index = 0, dirty = true, effects = [], result, id = 0;
    const doc = { hidden: false, hasFocus: () => true, addEventListener() {}, removeEventListener() {} };
    const element = { scrollHeight: 100, scrollTop: 0, clientHeight: 100, addEventListener() {}, removeEventListener() {} };
    const options = { enabled: true, active: true, selected: chat, thread: [{ messageId: 'm1' }], box: { current: element }, apiBaseUrl: '/test', headers: () => ({}) };
    const deferred = (target, payload) => new Promise((resolve) => target.push({ resolve, payload }));
    const { hook } = make((fn,deps) => {
        const i=index++, old=slots[i];
        if (old && deps.every((v,k) => Object.is(v,old.deps[k]))) return;
        slots[i]={ deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup=fn(); });
    }, (value) => slots[index++] ||= { current: value }, (initial) => {
        const i=index++; slots[i] ||= { value: typeof initial === 'function' ? initial() : initial };
        return [slots[i].value, (value) => { slots[i].value=typeof value === 'function' ? value(slots[i].value) : value; dirty=true; }];
    }, { get: (_url,config) => deferred(gets,config), post: (_url,payload) => deferred(posts,payload) }, pilotChatKey, doc, doc,
    (fn) => { timers.set(++id,fn); return id; }, (id) => timers.delete(id));
    const flush = async () => {
        for (let i=0;i<20;i++) {
            if (dirty) { dirty=false; index=0; result=hook(options); const pending=effects; effects=[]; pending.forEach((fn)=>fn()); }
            await Promise.resolve();
        }
    };
    return { options, gets, posts, doc, element, flush, get output() { return result; },
        async rerender() { dirty=true; await flush(); },
        async tick() { const pending=[...timers.values()]; timers.clear(); pending.forEach((fn)=>fn()); await flush(); },
        close() { slots.forEach((slot)=>slot?.cleanup?.()); },
    };
}

test('opening/scrolling never clears pending reply; manual ACK is single flight and race-safe', async () => {
    const h=harness(); await h.flush();
    h.output.apply([row(1)]); await h.flush();
    h.doc.hidden=true; await h.tick(); assert.equal(h.posts.length,0);
    h.doc.hidden=false;
    h.options.thread=[{ messageId:'m1' }]; await h.flush();
    // An incoming notification updates the ref before a scheduled React render.
    h.output.apply([row(2,2,'m2')]); await h.tick(); assert.equal(h.posts.length,0);
    h.options.thread=[{ messageId:'m2' }]; h.output.apply([row(2,2,'m2')]); await h.flush();
    await h.tick(); assert.equal(h.posts.length,0);
    const acknowledged=h.output.markRead(chat);
    assert.equal(h.posts.length,1); assert.equal(h.posts[0].payload.seenMessageId,'m2');
    const duplicate=h.output.markRead(chat); await duplicate; assert.equal(h.posts.length,1);
    h.output.apply([row(3,3,'m3')]); await h.flush();
    h.posts[0].resolve({ data: { item: row(2,0,'m2') } }); await acknowledged; await h.flush();
    assert.equal(h.output.items[key].unreadCount,3);
    h.close();
});

test('snapshot/stream interleave preserves new chats and changing account cancels old snapshot', async () => {
    const h=harness(); await h.flush(); const pending=h.output.refresh();
    h.output.apply([{ ...row(2), chat: { ...chat, contactName: 'Synthetic' } }]); await h.flush();
    h.gets[0].resolve({ data: { items:[row(1)], next:null } }); await pending; await h.flush();
    assert.equal(h.output.items[key].unreadVersion,2);
    assert.equal(h.output.items[key].chat.contactName,'Synthetic');
    const old=h.output.refresh(); h.options.enabled=false; h.output.apply([row(3)]); await h.flush();
    assert.equal(h.gets[1].payload.signal.aborted,true);
    h.gets[1].resolve({ data:{ items:[row(4)], next:null } }); await old; await h.flush();
    assert.deepEqual(h.output.items,{}); h.close();
});

// «Ответ не нужен» гаснет в момент ответа: своя очередь — сразу, чужой ответ — по
// строке архива pending; сервер (статус sent) потом подтверждает это своей строкой.
const echo = (id = 'w1', message = {}) => ({ account: 'op', ...chat, messageId: id, isEcho: true,
    status: message.status ?? 'pending',
    message: { messageId: id, isEcho: true, status: 'pending', clientMessageId: 'c1', authorName: 'Анна', authorId: '',
        isDeleted: false, ...message } });
const delivery = (status, id = 'w1') => ({ account: 'op', ...chat, messageId: id, isEcho: true, status, statusOnly: true });
const queued = (state = 'queued', extra = {}) => ({ clientMessageId: 'c1', account: 'op', ...chat, state, ...extra });

test('an in-flight reply hides only the row version it answered, while one of its sends is alive', () => {
    const { presentUnread, holdReply } = helpers;
    const replies = new Map();
    const items = { [key]: row(5, 2) };
    assert.equal(holdReply(replies, items, key, 'm:late', { active: true, now: 0 }), false, 'a status alone starts nothing');
    assert.equal(holdReply(replies, {}, key, 'c:1', { active: true, start: true, now: 0 }), false, 'nothing waits');
    assert.equal(holdReply(replies, items, key, 'c:1', { active: true, start: true, now: 0 }), true);
    assert.equal(presentUnread(items, replies, 1)[key].unreadCount, 0);
    assert.equal(presentUnread(items, replies, 60000)[key].unreadCount, 2, 'no confirmation within a minute');
    assert.equal(presentUnread({ [key]: row(6, 3, 'm2') }, replies, 1)[key].unreadCount, 3, 'a newer row wins');
    assert.equal(holdReply(replies, items, key, 'm:other', { active: true, now: 1 }), false);
    assert.equal(holdReply(replies, items, key, 'c:1', { active: false, now: 1 }), true);
    assert.equal(presentUnread(items, replies, 1)[key].unreadCount, 2, 'the failed send restores the queue');
    assert.equal(presentUnread(items, new Map(), 1), items, 'unchanged rows keep their identity');
});

test('stream: a pending operator echo starts a reply, error ends it; inbound, bots and unread rows start nothing', () => {
    const { replySignals } = helpers;
    assert.deepEqual(replySignals([echo()]), [{ key, ref: 'm:w1', active: true, start: true }]);
    assert.deepEqual(replySignals([delivery('sent'), delivery('error')]).map(({ active, start }) => [active, start]),
        [[true, false], [false, false]]);
    const starts = replySignals([
        { ...echo('in'), isEcho: false },
        echo('bot', { clientMessageId: null, authorName: ' ', authorId: '' }),
        echo('gone', { isDeleted: true }),
        echo('done', { status: 'sent' }), { ...echo('done2'), status: 'sent' },
        { ...row(9), messageId: `unread:${chat.channelId}:${chat.chatId}`, kind: 'unread' },
        { ...echo('note'), kind: 'note' },
    ]).filter(({ start }) => start);
    assert.deepEqual(starts, []);
});

test('own queue: queued starts at once, acceptance hands over to the message id, failure is not alive', () => {
    const { outboxSignals } = helpers;
    const seen = new Map();
    assert.deepEqual(outboxSignals([queued()], seen), [{ key, ref: 'c:c1', active: true, start: true }]);
    assert.deepEqual(outboxSignals([queued()], seen), [], 'the same snapshot repeats nothing');
    assert.deepEqual(outboxSignals([queued('sent', { messageId: 'w1' })], seen),
        [{ key, ref: 'c:c1', active: false, start: false }, { key, ref: 'm:w1', active: true, start: true }]);
    assert.deepEqual(outboxSignals([], seen), []); assert.equal(seen.size, 0);
    assert.deepEqual(outboxSignals([queued('failed'), { ...queued(), account: 'potok', clientMessageId: 'p' }], seen),
        [{ key, ref: 'c:c1', active: false, start: true }]);
});

test('«Ответ не нужен» goes out on send for the sender and the team, and comes back on failure', async () => {
    const h = harness(); h.options.outbox = []; await h.flush();
    h.output.apply([row(5, 2)]); await h.flush();
    assert.equal(h.output.items[key].unreadCount, 2); assert.equal(h.output.total, 2);
    h.options.outbox = [queued()]; await h.rerender();
    assert.equal(h.output.items[key].unreadCount, 0); assert.equal(h.output.total, 0);
    const shown = h.output.items; await h.rerender();
    assert.equal(h.output.items, shown, 'the same object while nothing changed');
    h.options.outbox = [queued('failed')]; await h.rerender();
    assert.equal(h.output.items[key].unreadCount, 2);
    // Another operator's reply: the archive row (pending) arrives over the stream.
    h.options.outbox = []; await h.rerender();
    h.output.apply([echo('w2')]); await h.flush();
    assert.equal(h.output.items[key].unreadCount, 0);
    h.output.apply([delivery('error', 'w2')]); await h.flush();
    assert.equal(h.output.items[key].unreadCount, 2);
    // Own reply accepted, its local copy settled, then Wazzup reports error.
    h.options.outbox = [{ ...queued(), clientMessageId: 'c3' }]; await h.rerender();
    h.options.outbox = [{ ...queued('sent', { messageId: 'w3' }), clientMessageId: 'c3' }]; await h.rerender();
    h.options.outbox = []; await h.rerender();
    assert.equal(h.output.items[key].unreadCount, 0);
    h.output.apply([delivery('error', 'w3')]); await h.flush();
    assert.equal(h.output.items[key].unreadCount, 2, 'the accepted reply failed later');
    h.close();
});

test('the server row takes over after the reply: sent clears, a new inbound shows, silence expires', async () => {
    const h = harness(); h.options.outbox = []; await h.flush();
    h.output.apply([row(5, 2)]); await h.flush();
    h.options.outbox = [queued()]; await h.rerender();
    h.options.outbox = [queued('sent', { messageId: 'w1' })]; await h.rerender();
    h.output.apply([echo('w1')]); await h.flush();
    h.options.outbox = []; await h.rerender();
    assert.equal(h.output.items[key].unreadCount, 0, 'no flash between the archive row and Wazzup «sent»');
    h.output.apply([delivery('sent'), row(6, 0)]); await h.flush();
    assert.equal(h.output.items[key].unreadCount, 0);
    h.output.apply([row(7, 1, 'm3')]); await h.flush();
    assert.equal(h.output.items[key].unreadCount, 1, 'the client wrote again after the answer');
    h.output.apply([echo('w4')]); await h.flush();
    assert.equal(h.output.items[key].unreadCount, 0);
    const realNow = Date.now;
    Date.now = () => realNow() + 61000;
    try { await h.tick(); } finally { Date.now = realNow; }
    assert.equal(h.output.items[key].unreadCount, 1, 'Wazzup never confirmed: the queue is back');
    h.close();
});
