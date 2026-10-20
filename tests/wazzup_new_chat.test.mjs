import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    defaultNewChatChannel, draftChat, newChatChannelOptions, normalizeNewChatPhone,
} from '../src/components/wazzup/newChat.js';

/* Кнопка «+» «Чатов ОП» — новый чат по номеру (владелец, 20.10.2026). */

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

test('номер приводится к международным цифрам, как его хранит Wazzup', () => {
    assert.equal(normalizeNewChatPhone('+7 (700) 123-45-67'), '77001234567');
    assert.equal(normalizeNewChatPhone('8 700 123 45 67'), '77001234567');
    assert.equal(normalizeNewChatPhone('700 123 45 67'), '77001234567', 'десять цифр без кода — казахстанский номер');
    assert.equal(normalizeNewChatPhone('+998 90 123 45 67'), '998901234567');
    for (const bad of ['', '  ', '12345', '+700123456', '0700123456', '7'.repeat(16), null, undefined]) {
        assert.equal(normalizeNewChatPhone(bad), '', String(bad));
    }
});

test('каналы «+» — только разрешённые сервером, в порядке колонки каналов', () => {
    const channels = [
        { channelId: 'global', name: 'Global Taxi' },
        { channelId: 'help', name: 'Help Taxi', plainId: '77070253322' },
        { channelId: 'jana', name: '', plainId: '77089001372' },
    ];
    const options = newChatChannelOptions(channels, ['jana', 'help']);
    assert.deepEqual(options, [{ value: 'help', label: 'Help Taxi' }, { value: 'jana', label: '77089001372' }]);
    assert.equal(defaultNewChatChannel(options, 'jana'), 'jana', 'открытый чат подсказывает канал');
    assert.equal(defaultNewChatChannel(options, 'global'), 'help', 'канал, где начинать нельзя, не подставляется');
    assert.deepEqual(newChatChannelOptions(channels, []), []);
});

test('заготовка чата — обычный чат раздела с флагом isNew', () => {
    assert.deepEqual(draftChat({ channelId: 'help', phone: '77001234567', name: '  Иван ' }), {
        channelId: 'help', chatId: '77001234567', chatType: 'whatsapp',
        contactName: 'Иван', contactPhone: '77001234567', isNew: true,
    });
    assert.equal(draftChat({ channelId: 'help', phone: '77001234567' }).contactName, null);
});

test('раздел: «+» у поиска, первое сообщение несёт newChat, у заготовки нет заметок, карточки и ссылки', () => {
    const view = read('src/components/wazzup/WazzupChatsView.jsx');
    assert.match(view, /<NewChatButton channels=\{channels\} allowedIds=\{newChatChannelIds\}/);
    assert.match(view, /newChatChannelIds \|\| \[\]/, 'каналы «+» считает сервер');
    assert.match(view, /\{ \.\.\.message, newChat: true, contactName: selected\.contactName \|\| undefined \}/);
    assert.match(view, /onSend=\{sendMessage\}/);
    assert.match(view, /firstContact=\{Boolean\(selected\.isNew\)\}/);
    assert.match(view, /typingEnabled=\{pilotCanSend && mainTab === 'chats' && !selected\.isNew\}/);
    assert.match(view, /const notesEnabled = pilot\.enabled && mainTab === 'chats' && Boolean\(selected\) && !selected\.isNew/);
    assert.match(view, /&& !selected\.isNew && !GLOBAL_CHANNEL_IDS\.has\(selected\.channelId\)/);
    assert.match(view, /syncWazzupChatDeepLink\(selected\?\.isNew \? null : selected, account\)/);
    assert.match(view, /&& !writeLock && newChatChannelIds\.length > 0/, '«+» — тоже письмо клиенту: только в «Активном»');
});

test('поле ответа: у заготовки сразу подсказка про шаблон WABA и нет скрепки', () => {
    const composer = read('src/components/wazzup/ChatPilotComposer.jsx');
    assert.match(composer, /const showWabaHint = windowExpired \|\| \(firstContact && channelTransport === 'wapi'\);/);
    assert.match(composer, /\{!firstContact && <>\s*<input ref=\{fileInputRef\}/);
});
