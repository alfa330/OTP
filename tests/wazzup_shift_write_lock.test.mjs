/*
 * «Чаты ОП»: верификатор пишет клиентам только в статусе «Активный» (владелец
 * 19.10.2026). Здесь — правило (workspaceStatus.js), строка на месте поля ввода
 * (ShiftWriteLock.jsx) и проводка в разделе. Отказ сервера — tests/test_wazzup_pilot.py.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { shiftWriteLock } from '../src/components/wazzup/workspaceStatus.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const ACTIVE = { key: 'готов', label: 'Активный', tone: 'work', onShift: true };
const BREAK = { key: 'перерыв', label: 'Перерыв', tone: 'break', onShift: true };

test('правило: писать можно только в «Активном» — ключ начала смены знает сервер', () => {
    assert.equal(shiftWriteLock(ACTIVE, 'готов'), null);
    for (const current of [BREAK, { key: 'тренинг', label: 'Тренинг', tone: 'training' },
        { key: 'тех причина', label: 'Тех. пауза', tone: 'tech' }, { key: 'исход', label: 'Исход', tone: 'other' }]) {
        assert.equal(shiftWriteLock(current, 'готов'), current, current.key);
    }
    // Нет статуса или ключа — раздел в этом состоянии чатов не показывает: замка нет.
    assert.equal(shiftWriteLock(null, 'готов'), null);
    assert.equal(shiftWriteLock(BREAK, ''), null);
    // Ключ — от сервера: переименуют статус начала смены — правило пойдёт за ним.
    assert.equal(shiftWriteLock(BREAK, 'перерыв'), null);
});

// Строка — настоящий компонент; хранилище смены и иконки подменены.
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzup-shift-write-lock.mjs');
await build({
    entryPoints: ['src/components/wazzup/ShiftWriteLock.jsx'], outfile: output,
    bundle: true, format: 'esm', platform: 'node', jsx: 'automatic', external: ['react', 'react/jsx-runtime'],
    plugins: [{ name: 'write-lock', setup(builder) {
        builder.onResolve({ filter: /^(\.\/workspaceStore|lucide-react)$/ }, ({ path }) => ({ path, namespace: 'lock' }));
        builder.onLoad({ filter: /.*/, namespace: 'lock' }, ({ path }) => ({ loader: 'js', contents: {
            './workspaceStore': `export const useWorkspace=()=>globalThis.__lock.state;
                export const setWorkspaceStatus=(key)=>{ globalThis.__lock.calls.push(key); return Promise.resolve(true); };`,
            'lucide-react': 'export const Loader2=()=>"[spin]", Play=()=>"[play]";',
        }[path] }));
    } }],
});
const { default: ShiftWriteLock } = await import(pathToFileURL(output));

const STATUSES = [
    { key: 'готов', label: 'Активный', tone: 'work' },
    { key: 'перерыв', label: 'Перерыв', tone: 'break' },
];

function lock(state) {
    globalThis.__lock = { calls: [], state: { statuses: STATUSES, startKey: 'готов', current: BREAK, pending: false, ...state } };
    return globalThis.__lock;
}

const findButton = (node) => {
    if (!node || typeof node !== 'object') return null;
    if (node.type === 'button') return node;
    const children = [].concat(node.props?.children ?? []);
    for (const child of children) {
        const found = findButton(child);
        if (found) return found;
    }
    return null;
};

test('строка: текущий статус, правило словами сервера и кнопка возврата в «Активный»', () => {
    lock();
    const html = renderToStaticMarkup(React.createElement(ShiftWriteLock));
    assert.match(html, /data-testid="wazzup-write-lock"/);
    assert.match(html, /role="status"/);
    assert.match(html, /<span class="font-semibold text-slate-800">Перерыв<\/span> · писать клиентам можно только в статусе «Активный»/);
    assert.match(html, /\[play\]Вернуться в «Активный»<\/button>/);
    // Те же классы, что у поля ввода: темы окна перекрашивают строку сами.
    assert.match(html, /class="shrink-0 border-t border-slate-200\/70 bg-white p-2\.5 sm:px-4"/);
    // Точка — цвета статуса, как в меню статусов (перерыв — янтарный).
    assert.match(html, /rounded-full bg-amber-500/);
});

test('строка: кнопка ставит статус начала смены, пока статус сохраняется — ждёт', () => {
    const h = lock();
    const button = findButton(ShiftWriteLock());
    assert.equal(button.props.disabled, false);
    button.props.onClick();
    assert.deepEqual(h.calls, ['готов']);
    const pending = lock({ pending: true });
    const busy = findButton(ShiftWriteLock());
    assert.equal(busy.props.disabled, true);
    assert.match(renderToStaticMarkup(React.createElement(ShiftWriteLock)), /\[spin\]Вернуться/);
    assert.deepEqual(pending.calls, []);
    // Подпись «Активного» — из каталога сервера, не копия в коде.
    lock({ statuses: [{ key: 'готов', label: 'На линии', tone: 'work' }] });
    assert.match(renderToStaticMarkup(React.createElement(ShiftWriteLock)),
        /только в статусе «На линии».*Вернуться в «На линии»/);
});

test('раздел: поле ввода и «Ответить» — только без замка, строка — там, где поле было бы', () => {
    const view = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8').replace(/\r\n/g, '\n');
    assert.match(view, /import ShiftWriteLock from '\.\/ShiftWriteLock';/);
    assert.match(view, /import \{ shiftWriteLock \} from '\.\/workspaceStatus';/);
    assert.match(view, /operator = false, potok = false, toolbar = null, entering = false, writeLock = null \} = props;/);
    assert.match(view, /const pilotChannelCanSend = pilot\.enabled && pilot\.capability\?\.canSend && selected\?\.chatType === 'whatsapp'\n/);
    assert.match(view, /const pilotCanSend = pilotChannelCanSend && !writeLock;/);
    assert.match(view, /onReply=\{pilotCanSend && !m\.local \? chooseReply : undefined\}/);
    // Заметка открыта — она и остаётся; иначе замок, и только без него — поле ввода.
    assert.match(view, /\) : writeLock && pilotChannelCanSend \? \(\n\s*<ShiftWriteLock \/>\n\s*\) : pilotCanSend && \(\n\s*<ChatPilotComposer /);
    assert.equal(view.split('<ChatPilotComposer ').length - 1, 1, 'поле ввода одно');
    // Замок — только у верификатора, по его текущему статусу и ключу начала смены от сервера.
    assert.match(view, /return <ChatsWorkspace \{\.\.\.props\} operator entering=\{startedHere\.current\} toolbar=\{<ShiftStatusMenu \/>\}\n\s*writeLock=\{shiftWriteLock\(workspace\.current, workspace\.startKey\)\} \/>;/);
    assert.equal(view.split('writeLock={').length - 1, 1, 'остальным замок не передаётся');
});
