import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    adoptEmbedIdentityFromSearch, installEmbedBridge, isChatsEmbedSearch, parseEmbedCommand, parseEmbedIdentity,
    queueEmbedCommand, replyToEmbedParent, resetEmbedBridgeForTests, setEmbedStatus, takeEmbedCommands,
    takeEmbedIdentity,
} from '../src/components/wazzup/embedBridge.js';

/* «Чаты ОП» окном в CRM «Потока» (владелец, 20.10.2026): команды страницы-хозяина. */

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8').replace(/\r\n/g, '\n');

const frame = () => {
    const posted = [];
    const listeners = [];
    const parent = { postMessage: (message, origin) => posted.push({ message, origin }) };
    const win = { parent, addEventListener: (type, listener) => { if (type === 'message') listeners.push(listener); } };
    const send = (data, { source = parent, origin = 'https://crm.example.test' } = {}) =>
        listeners.forEach((listener) => listener({ data, source, origin }));
    return { win, parent, posted, send };
};

test('окно включает одна метка embed=1', () => {
    assert.equal(isChatsEmbedSearch('?view=wazzup_chats&embed=1'), true);
    assert.equal(isChatsEmbedSearch('?embed=1&view=hours'), true, 'вид в адресе портал может переписать сам');
    assert.equal(isChatsEmbedSearch('?view=wazzup_chats'), false);
    assert.equal(isChatsEmbedSearch('?embed=0'), false);
    assert.equal(isChatsEmbedSearch(''), false);
});

test('команда «открыть»: номер в международные цифры, мусор отсекается', () => {
    assert.deepEqual(parseEmbedCommand({ type: 'icore.chats.open', phone: '8 (700) 123-45-67', name: ' Иван ', requestId: 7 }),
        { requestId: '7', command: { phone: '77001234567', name: 'Иван', requestId: '7', channelId: '' } });
    assert.equal(parseEmbedCommand({ type: 'icore.chats.open', phone: 77001234567 }).command.phone, '77001234567');
    assert.equal(parseEmbedCommand({ type: 'icore.chats.open', phone: '7', channelId: 'x/../y' }).error.code, 'BAD_PHONE');
    assert.equal(parseEmbedCommand({ type: 'icore.chats.open', phone: '+77001234567', channelId: 'bad id' }).command.channelId, '');
    assert.equal(parseEmbedCommand({ type: 'other', phone: '77001234567' }), null);
    assert.equal(parseEmbedCommand('icore.chats.open'), null);
    assert.equal(parseEmbedCommand(null), null);
});

test('команды — только от страницы-хозяина; ответ с номером — туда, откуда пришла команда', () => {
    resetEmbedBridgeForTests();
    const { win, posted, send } = frame();
    assert.equal(installEmbedBridge(win), true);
    const received = [];
    const release = takeEmbedCommands((command) => received.push(command));
    send({ type: 'icore.chats.open', phone: '77001234567' }, { source: {} });
    assert.equal(received.length, 0, 'чужое окно команду не даёт');
    send({ type: 'icore.chats.open', phone: '77001234567', requestId: 'a1' });
    assert.deepEqual(received.map((command) => command.phone), ['77001234567']);
    replyToEmbedParent('opened', { requestId: 'a1', phone: '77001234567', channelId: 'c', chatId: '77001234567', isNew: true });
    assert.equal(posted.at(-1).origin, 'https://crm.example.test');
    assert.equal(posted.at(-1).message.type, 'icore.chats.opened');
    send({ type: 'icore.chats.open', phone: 'нет номера', requestId: 'a2' });
    assert.deepEqual(posted.at(-1).message, { type: 'icore.chats.error', requestId: 'a2', code: 'BAD_PHONE',
        message: 'Номер не распознан: нужен полный номер, например +7 700 123 45 67' });
    release();
    resetEmbedBridgeForTests();
});

test('команда до готовности ждёт; состояние шлётся только при смене', () => {
    resetEmbedBridgeForTests();
    const { win, posted, send } = frame();
    installEmbedBridge(win);
    setEmbedStatus('login');
    setEmbedStatus('login');
    send({ type: 'icore.chats.open', phone: '77001234567' });
    send({ type: 'icore.chats.open', phone: '77001234568' });
    setEmbedStatus('ready');
    assert.deepEqual(posted.map((item) => item.message.status).filter(Boolean), ['login', 'ready']);
    const received = [];
    takeEmbedCommands((command) => received.push(command.phone));
    assert.deepEqual(received, ['77001234568'], 'ждёт последняя команда');
    queueEmbedCommand({ phone: '77001234569' });
    assert.deepEqual(received, ['77001234568', '77001234569']);
    resetEmbedBridgeForTests();
});

test('вне iframe мост не слушает, но номер из адреса окна всё равно открывается', () => {
    resetEmbedBridgeForTests();
    const win = { addEventListener: () => assert.fail('слушать некого') };
    win.parent = win;
    assert.equal(installEmbedBridge(win), false);
    const received = [];
    queueEmbedCommand({ phone: '77001234567' });
    takeEmbedCommands((command) => received.push(command.phone));
    assert.deepEqual(received, ['77001234567']);
    resetEmbedBridgeForTests();
});

test('портал: окно — один раздел без меню, со сторожем смены; номер из адреса — команда', () => {
    const app = read('src/App.jsx');
    assert.match(app, /const CHATS_EMBED = typeof window !== 'undefined' && isChatsEmbedSearch\(window\.location\.search\);\nif \(CHATS_EMBED\) \{\n\s*installEmbedBridge\(\);[\s\S]{0,120}adoptEmbedIdentityFromSearch\(window\.location\.search\);\n\}/);
    const screen = app.slice(app.indexOf('            if (CHATS_EMBED) {\n                return ('), app.indexOf('                <div className="flex h-screen overflow-hidden">'));
    assert.match(screen, /<WazzupChatsView[\s\S]*embed\n\s*onLogout=\{handleLogout\}/);
    assert.match(screen, /<WazzupShiftKeeper/);
    assert.doesNotMatch(screen, /sidebarTree|NotificationBell|MobileTabBar/);
    assert.match(app, /if \(!user\?\.id\) setEmbedStatus\('login'\);\n\s*else if \(!canAccessVerifierChatsSection\) setEmbedStatus\('no_access'\);/);
    assert.match(app, /queueEmbedCommand\(\{ phone: wazzupInitialChat\.phone, name: '', channelId: '', requestId: '' \}\);/);
});

test('раздел: команда открывает свой чат, при нескольких каналах — выбор, иначе новый чат', () => {
    const view = read('src/components/wazzup/WazzupChatsView.jsx');
    const handler = view.slice(view.indexOf('    const openForEmbed = async'), view.indexOf('    const openForEmbedRef'));
    assert.match(handler, /params: \{ account: 'op', chat_id: phone, limit: 20 \}/);
    assert.match(handler, /if \(rows\.length === 1\) \{\n\s*openChat\(rows\[0\]\);/);
    assert.match(handler, /showNumberChats\(phone\);\n\s*reply\('pending', \{ reason: 'several_chats' \}\);/);
    assert.match(handler, /const allowed = canStartChats \? newChatChannelIds : \[\];/);
    assert.match(handler, /openChat\(draftChat\(\{ channelId: target, phone, name \}\)\);/);
    assert.match(handler, /setNewChatRequest\(\{ phone, name, nonce: Date\.now\(\) \}\);/);
    assert.doesNotMatch(handler, /enqueueMessage|\/pilot\/send/, 'команда CRM ничего не отправляет за человека');
    assert.match(view, /return takeEmbedCommands\(\(command\) => openForEmbedRef\.current\(command\)\);/);
    assert.match(view, /useEffect\(\(\) => \{ if \(embed\) setEmbedStatus\('locked'\); \}, \[embed\]\);/);
});

test('пользователь CRM: из адреса окна с подписью iCORE; без подписи — не берём', () => {
    resetEmbedBridgeForTests();
    const sig = 'a'.repeat(64);
    const proof = { crmExp: '1790003600', crmSig: sig };
    assert.deepEqual(parseEmbedIdentity({ crmUserId: 352, crmUserName: ' Иванова Айгерим ', ...proof }),
        { crmUserId: '352', crmUserName: 'Иванова Айгерим', ...proof });
    assert.equal(parseEmbedIdentity({ crmUserId: '352', crmUserName: 'Иван' }), null, 'без подписи пары нет');
    assert.equal(parseEmbedIdentity({ crmUserId: '352', crmExp: 'soon', crmSig: sig }), null);
    assert.equal(parseEmbedIdentity({ crmUserId: '352', crmExp: '1790003600', crmSig: 'xyz' }), null);
    assert.equal(parseEmbedIdentity({ crmUserId: 'a b', ...proof }), null);
    adoptEmbedIdentityFromSearch('?embed=1&crmUserId=352&crmUserName=Иван');
    const seen = [];
    takeEmbedIdentity((identity) => seen.push(identity));
    assert.deepEqual(seen, [], 'адрес без подписи пару не даёт');
    adoptEmbedIdentityFromSearch(`?embed=1&crmUserId=352&crmUserName=%D0%98%D0%B2%D0%B0%D0%BD&crmExp=1790003600&crmSig=${sig}`);
    assert.deepEqual(seen, [{ crmUserId: '352', crmUserName: 'Иван', ...proof }]);
    const { win, posted, send } = frame();
    installEmbedBridge(win);
    send({ type: 'icore.chats.identify', crmUserId: '352', crmUserName: 'Иван', ...proof });
    assert.equal(seen.length, 1, 'та же пара — без нового запроса');
    send({ type: 'icore.chats.identify', crmUserId: '401', crmUserName: 'Пётр', crmExp: '1790003600', crmSig: 'b'.repeat(64) });
    assert.equal(seen.at(-1).crmUserId, '401');
    send({ type: 'icore.chats.identify', crmUserId: '402' });
    assert.equal(posted.at(-1).message.code, 'BAD_IDENTITY');
    resetEmbedBridgeForTests();
});

test('раздел: пару «учётка iCORE ↔ пользователь CRM» передаёт тот, кто пишет', () => {
    const view = read('src/components/wazzup/WazzupChatsView.jsx');
    const effect = view.slice(view.indexOf('        if (!embedReady || !pilot.enabled) return undefined;'));
    assert.match(effect, /return takeEmbedIdentity\(\(identity\) => \{/);
    assert.match(effect, /axios\.post\(`\$\{apiBaseUrl\}\/api\/wazzup\/pilot\/crm-identity`, identity, \{ headers: headers\(\) \}\)/);
    assert.match(effect, /replyToEmbedParent\('identified', \{ crmUserId: identity\.crmUserId \}\)/);
});
