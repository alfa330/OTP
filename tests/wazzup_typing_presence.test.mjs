import test from 'node:test';
import assert from 'node:assert/strict';
import { createChatTypingStore, typingLabel } from '../src/components/wazzup/chatTypingPresence.js';
import { pilotChatKey } from '../src/components/wazzup/chatPilot.js';

const chat = { channelId: 'channel', chatId: 'chat' };
const key = pilotChatKey('op', chat);
const event = (updates = {}) => ({ kind: 'typing', account: 'op', ...chat, userId: '2',
    authorName: 'Анна', clientId: 'tab-1', typing: true, emittedAt: 1000, expiresAt: 9000, sequence: 1, ...updates });

function fixture() {
    let time = 1000, id = 0;
    const timers = new Map();
    const store = createChatTypingStore({ ownerId: 1, now: () => time,
        schedule: (callback, delay) => { timers.set(++id, { callback, at: time + delay }); return id; },
        cancel: (timer) => timers.delete(timer) });
    return { store, timers, tick(ms) {
        const end = time + ms;
        for (;;) {
            const next = [...timers].filter(([, task]) => task.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
            if (!next) break;
            const [timer, task] = next;
            time = task.at; timers.delete(timer); task.callback();
        }
        time = end;
    } };
}

test('heartbeats only extend expiry; only affected chat indicators are notified', () => {
    const { store, timers, tick } = fixture();
    let affected = 0, unrelated = 0;
    store.subscribe(key, () => affected++);
    store.subscribe(pilotChatKey('op', { ...chat, chatId: 'other' }), () => unrelated++);
    store.apply([event()]);
    assert.equal(store.snapshot(key), 'Анна печатает…');
    tick(3000);
    store.apply([event({ emittedAt: 4000, expiresAt: 12000, sequence: 2 })]);
    assert.equal(affected, 1);
    assert.equal(unrelated, 0);
    assert.equal(timers.size, 1);
    tick(7999); assert.equal(store.snapshot(key), 'Анна печатает…');
    tick(1); assert.equal(store.snapshot(key), '');
    assert.equal(affected, 2);
    tick(60000); assert.equal(timers.size, 0);
});

test('operators aggregate by user, tabs stop independently and own activity is hidden', () => {
    const { store } = fixture();
    store.apply([event(), event({ clientId: 'tab-2' }), event({ userId: 1, authorName: 'Я' }),
        event({ userId: '3', authorName: 'Иван' })]);
    assert.equal(store.snapshot(key), 'Анна и Иван печатают…');
    store.apply([event({ typing: false, expiresAt: 1000, emittedAt: 1001, sequence: 2 })]);
    assert.equal(store.snapshot(key), 'Анна и Иван печатают…');
    store.apply([event({ typing: false, clientId: 'tab-2', expiresAt: 1000, emittedAt: 1001, sequence: 2 })]);
    assert.equal(store.snapshot(key), 'Иван печатает…');
    store.clear(); assert.equal(store.snapshot(key), '');
});

test('expired replay, other accounts, invalid data and out-of-order starts cannot show presence', () => {
    const { store } = fixture();
    store.apply([event({ expiresAt: 999 }), event({ account: 'potok' }), event({ kind: 'note' }),
        event({ typing: 'true' }), event({ expiresAt: NaN }), event({ channelId: '' })]);
    assert.equal(store.snapshot(key), '');
    // Stop may arrive first, for example from a different server process.
    store.apply([event({ typing: false, sequence: 2, expiresAt: 1000 })]);
    store.apply([event({ sequence: 1, emittedAt: 1500 })]);
    assert.equal(store.snapshot(key), '');
    store.apply([event({ sequence: 3, emittedAt: 1002 })]);
    assert.equal(store.snapshot(key), 'Анна печатает…');
    store.clear();
});

test('server-relative TTL handles both clock skew directions and caps leases', () => {
    for (const serverTime of [-300000, 300000]) {
        const { store, tick } = fixture();
        store.apply([event({ emittedAt: serverTime - 3000, expiresAt: serverTime + 5000 })], serverTime);
        assert.equal(store.snapshot(key), 'Анна печатает…');
        tick(5000); assert.equal(store.snapshot(key), '');
        store.apply([event({ sequence: 2, expiresAt: serverTime + 999999 })], serverTime);
        tick(8000); assert.equal(store.snapshot(key), '');
        store.clear();
    }
});

test('disconnect hides presence but preserves versions against stale starts after reconnect', () => {
    const { store } = fixture();
    store.apply([event({ sequence: 2 })]);
    store.hide(); assert.equal(store.snapshot(key), '');
    store.apply([event({ sequence: 1, emittedAt: 1500 })]);
    assert.equal(store.snapshot(key), '');
    store.apply([event({ sequence: 3, emittedAt: 2000 })]);
    assert.equal(store.snapshot(key), 'Анна печатает…');
    store.clear();
});

test('clearing on disconnect removes labels and all scheduled work', () => {
    const { store, timers } = fixture();
    let renders = 0;
    const unsubscribe = store.subscribe(key, () => renders++);
    store.apply([event()]); store.clear();
    assert.equal(store.snapshot(key), '');
    assert.equal(timers.size, 0);
    assert.equal(renders, 2);
    unsubscribe(); store.apply([event()]);
    assert.equal(renders, 2);
    store.clear();
});

test('session memory stays bounded under many distinct senders', () => {
    const { store, timers } = fixture();
    store.apply(Array.from({ length: 700 }, (_, i) => event({ clientId: String(i), chatId: String(i) })));
    assert.equal(store.snapshot(pilotChatKey('op', { ...chat, chatId: '0' })), '');
    assert.equal(store.snapshot(pilotChatKey('op', { ...chat, chatId: '699' })), 'Анна печатает…');
    assert.equal(timers.size, 1);
    store.clear();
});

test('labels name simultaneous operators with readable plural forms', () => {
    assert.equal(typingLabel([]), '');
    assert.equal(typingLabel(['Анна', 'Иван', 'Олег']), 'Анна, Иван и ещё 1 печатают…');
});
