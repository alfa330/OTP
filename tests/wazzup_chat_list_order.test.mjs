import test from 'node:test';
import assert from 'node:assert/strict';
import { pilotChatKey } from '../src/components/wazzup/chatPilot.js';
import { orderChatList, waitingCount } from '../src/components/wazzup/chatListOrder.js';

// Chat list order: chats where a client waits for the team's reply stay on top,
// the rest by the last message time. No network, no React.
const at = (minutes) => new Date(Date.UTC(2026, 9, 9, 8, minutes)).toISOString();
const row = (chatId, minutes, extra = {}) => ({ channelId: 'ch-a', chatId, lastMessageAt: at(minutes),
    contactName: `Клиент ${chatId}`, contactPhone: chatId, ...extra });
const live = (chat, unreadCount, extra = {}) => ({ [pilotChatKey('op', chat)]: { channelId: chat.channelId, chatId: chat.chatId,
    unreadCount, unreadVersion: 1, lastInboundId: 'm', chat, ...extra } });
const ids = (list) => list.map((chat) => chat.chatId);

const answeredFresh = row('77000000001', 30);
const waitingOld = row('77000000002', 10, { unreadCount: 1 });
const quiet = row('77000000003', 5);
const loaded = [answeredFresh, waitingOld, quiet];

test('before the live counter arrives the server hint already puts waiting chats on top', () => {
    assert.deepEqual(ids(orderChatList(loaded, { items: {}, ready: false })),
        ['77000000002', '77000000001', '77000000003']);
});

test('the live counter wins over the hint: an answer returns the chat to its time, a new message lifts it', () => {
    const answered = { items: live(waitingOld, 0), ready: true };
    assert.deepEqual(ids(orderChatList(loaded, answered)), ['77000000001', '77000000002', '77000000003']);
    // After the snapshot a chat absent from the counter is not waiting, whatever the stale hint says.
    assert.deepEqual(ids(orderChatList(loaded, { items: {}, ready: true })), ['77000000001', '77000000002', '77000000003']);
    const lifted = { items: live(quiet, 2), ready: true };
    assert.deepEqual(ids(orderChatList(loaded, lifted)), ['77000000003', '77000000001', '77000000002']);
    assert.equal(waitingCount(quiet, lifted), 2);
    assert.equal(waitingCount(waitingOld, lifted), 0);
});

test('a waiting chat outside the loaded pages is shown on top if it matches the filter', () => {
    const elsewhere = row('77000000009', 1, { channelId: 'ch-b', contactName: 'Алия' });
    const unread = { items: live(elsewhere, 1), ready: true };
    assert.deepEqual(ids(orderChatList(loaded, unread)), ['77000000009', '77000000001', '77000000002', '77000000003']);
    assert.deepEqual(ids(orderChatList(loaded, unread, { channelId: 'ch-a' })), ['77000000001', '77000000002', '77000000003']);
    assert.deepEqual(ids(orderChatList(loaded, unread, { search: 'алия' })), ['77000000009', '77000000001', '77000000002', '77000000003']);
    assert.deepEqual(ids(orderChatList(loaded, unread, { search: 'нет такого' })), ['77000000001', '77000000002', '77000000003']);
});

test('several waiting chats: newest first among them; the loaded row wins over the counter copy', () => {
    const fuller = { ...waitingOld, lastMessageText: 'полная строка' };
    const unread = { items: { ...live(row('77000000002', 10), 1), ...live(quiet, 1) }, ready: true };
    const list = orderChatList([answeredFresh, fuller, quiet], unread);
    assert.deepEqual(ids(list), ['77000000002', '77000000003', '77000000001']);
    assert.equal(list[0].lastMessageText, 'полная строка');
});

test('"waiting only" shows just the top block; loading stays loading; other accounts keep the server order', () => {
    const unread = { items: live(quiet, 1), ready: true };
    assert.deepEqual(ids(orderChatList(loaded, unread, { unreadOnly: true })), ['77000000003']);
    assert.equal(orderChatList(null, unread), null);
    assert.deepEqual(ids(orderChatList(null, unread, { unreadOnly: true })), ['77000000003']);
    assert.equal(orderChatList(loaded, null), loaded);
    assert.equal(waitingCount(waitingOld, null), 0);
});
