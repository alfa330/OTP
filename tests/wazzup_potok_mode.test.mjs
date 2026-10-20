import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

/* Группа «Поток» в «Чатах ОП» (владелец, 20.10.2026): только канал +77070253322,
   доступ — обычным QR от СВ, без смены. Каналы режет сервер; здесь — проводка. */

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
const view = read('src/components/wazzup/WazzupChatsView.jsx');
const app = read('src/App.jsx');

test('раздел: «Поток» — замок с QR и окно чатов без смены', () => {
    const potok = view.slice(view.indexOf('function PotokWorkspace('), view.indexOf('export default function WazzupChatsView('));
    assert.match(potok, /if \(workspace\.locked\) return <div className="wazzup-scrollbar h-full overflow-y-auto"><ChatAccessGate \/><\/div>;/);
    assert.match(potok, /return <ChatsWorkspace \{\.\.\.props\} operator potok \/>;/);
    assert.doesNotMatch(potok, /ShiftStartScreen|ShiftStatusMenu|writeLock/, 'смены у «Потока» нет');
    assert.match(view, /if \(potok\) return <PotokWorkspace key=\{props\.user\?\.id\} \{\.\.\.props\} \/>;/);
    assert.match(view, /props\.user\?\.wazzup_chat_potok \?\? props\.user\?\.wazzupChatPotok/);
});

test('раздел: у «Потока» нет карточек водителя и их вкладки', () => {
    assert.match(view, /const driverCardEnabled = account === 'op' && !potok &&/);
    assert.match(view, /\{operator && !potok && \(/);
    assert.match(view, /\{account === 'op' && !potok && \(\s*<div className="wazzup-scrollbar min-h-0 flex-1 overflow-y-auto p-4 sm:p-6"\s*style=\{\{ display: mainTab === 'cards'/);
});

test('портал: пункт меню и счётчик ожидающих — и у «Потока», сторож смены — только у верификатора', () => {
    assert.match(app, /const isWazzupChatPotok = \(userLike\) => Boolean\(\s*userLike\?\.wazzup_chat_potok \?\? userLike\?\.wazzupChatPotok\s*\);/);
    assert.match(app, /if \(isWazzupChatOperator\(userLike\) \|\| isWazzupChatPotok\(userLike\)\) return true;/);
    assert.match(app, /operator=\{isWazzupChatOperator\(user\) \|\| isWazzupChatPotok\(user\)\}/);
    assert.match(app, /\{isWazzupChatOperator\(user\) && \(\s*<Suspense fallback=\{null\}>\s*<WazzupShiftKeeper/);
});
