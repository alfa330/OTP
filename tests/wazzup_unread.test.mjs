import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pilotChatKey } from '../src/components/wazzup/chatPilot.js';

const source = readFileSync(new URL('../src/components/wazzup/useSharedChatUnread.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replaceAll('export function', 'function').replace('export default function', 'function');
const make = new Function('useEffect','useRef','useState','axios','pilotChatKey','document','window','setTimeout','clearTimeout',
    source + '\nreturn { hook: useSharedChatUnread, mergeUnread, reconcileUnreadSnapshot };');
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
