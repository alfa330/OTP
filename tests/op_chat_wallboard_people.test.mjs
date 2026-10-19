/*
 * «Табло ОП» · «Чат»: люди таблицей внизу, как «Сотрудники» у «Линии» (владелец 19.10.2026) —
 * статус смены с временем в нём, время входа, счётчики чатов и время ответа. Здесь настоящий
 * компонент отрисовывается через react-dom/server; график (recharts) и иконки подменены.
 * Снимок с сервера — tests/test_op_chat_wallboard.py.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

// React и react-dom/server собраны в тот же модуль, что и компонент: одна копия React на оба.
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'op-chat-wallboard-people.mjs');
await build({
    stdin: {
        contents: `import React from 'react';
            import { renderToStaticMarkup } from 'react-dom/server';
            import { VerifiersTable } from './src/components/monitoring/OpChatWallboard.jsx';
            export const renderTable = (props) => renderToStaticMarkup(React.createElement(VerifiersTable, props));`,
        resolveDir: process.cwd(), loader: 'jsx', sourcefile: 'people-entry.jsx',
    },
    outfile: output, bundle: true, format: 'esm', platform: 'node', jsx: 'automatic',
    define: { 'process.env.NODE_ENV': '"production"' }, logLevel: 'error',
    // react-dom/server берёт модули node (stream) через require — даём его ESM-сборке.
    banner: { js: "import { createRequire as __otpRequire } from 'node:module'; const require = __otpRequire(import.meta.url);" },
    plugins: [{ name: 'people', setup(builder) {
        builder.onResolve({ filter: /^(recharts|\.\.\/common\/FaIcon)$/ }, ({ path }) => ({ path, namespace: 'stub' }));
        builder.onLoad({ filter: /.*/, namespace: 'stub' }, ({ path }) => ({ loader: 'js', contents: path === 'recharts'
            ? ['Bar', 'CartesianGrid', 'ComposedChart', 'LabelList', 'Legend', 'Line', 'ReferenceLine',
                'ResponsiveContainer', 'Tooltip', 'XAxis', 'YAxis'].map((name) => `export const ${name} = () => null;`).join(' ')
            : 'export default ({ className }) => `[icon ${className}]`;' }));
    } }],
});
const { renderTable } = await import(pathToFileURL(output));

const person = (patch) => ({
    user_id: 1, id: 1, name: 'Иванова Айгерим', in_roster: true, in_work: 0, chats: 0,
    first_reply_seconds: null, inner_reply_seconds: null, status_key: 'unknown', status_label: 'Нет событий',
    status_weight: 90, status_seconds: null, status_at: '', entry_at: null, ...patch,
});

const render = (operators, extra = {}) => renderTable({
    snapshot: { day: '2026-10-19', now: { operators } }, firstTarget: 60, innerTarget: 240,
    onOpenJournal: () => {}, ...extra,
});

const cells = (html, name) => {
    const row = html.split('<tr').find((piece) => piece.includes(`>${name}<`));
    assert.ok(row, name);
    return [...row.matchAll(/<td[^>]*>(.*?)<\/td>/g)].map((match) => match[1].replace(/<[^>]+>/g, ''));
};

test('столбцы — как у «Линии», но про чаты', () => {
    const html = render([person({ status_key: 'free', status_label: 'Активный', status_weight: 20 })]);
    const heads = [...html.matchAll(/<th(?:\s[^>]*)?>(.*?)<\/th>/g)].map((match) => match[1]);
    assert.deepEqual(heads, ['Сотрудник', 'Статус', 'Время входа', 'В работе', 'Чатов за сутки',
        'Первый ответ, мин', 'Внутри чата, мин', '']);
});

test('строка: статус с временем в нём, вход, чаты и время ответа по норме', () => {
    const html = render([
        person({ id: 1, user_id: 1, name: 'Иванова Айгерим', status_key: 'free', status_label: 'Активный',
            status_weight: 20, status_seconds: 7205, entry_at: '2026-10-19T07:48:06', in_work: 2, chats: 9,
            first_reply_seconds: 45, inner_reply_seconds: 300 }),
        person({ id: 2, user_id: 2, name: 'Петров Данияр', status_key: 'break', status_label: 'Перерыв',
            status_weight: 60, status_seconds: 600, entry_at: '2026-10-18T21:05:00', chats: 3,
            first_reply_seconds: 90, inner_reply_seconds: 120 }),
    ]);
    assert.deepEqual(cells(html, 'Иванова Айгерим'),
        ['Иванова Айгерим', 'Активный2:00:05', '07:48', '2', '9', '0,8', '5,0', '[icon fas fa-chevron-right]']);
    // Ночная смена с вечера — время входа вчерашнее, с пометкой.
    assert.deepEqual(cells(html, 'Петров Данияр').slice(1, 4), ['Перерыв10:00', '21:05вчера', '0']);
    // Цвет статуса — те же чипы, что у «Линии»; время ответа — по норме (минута, четыре).
    assert.match(html, /bg-green-100 text-green-700[^>]*>Активный/);
    assert.match(html, /bg-orange-100 text-orange-700[^>]*>Перерыв/);
    assert.match(html, /text-emerald-600">0,8</);   // 45 с — в норме минуты
    assert.match(html, /text-rose-600">5,0</);      // 300 с — дольше четырёх минут
    assert.match(html, /text-rose-600">1,5</);      // первый ответ 90 с — дольше минуты
    // ФИО — кнопка журнала, как у «Линии».
    assert.match(html, /<button type="button" title="Журнал статусов"[^>]*>Иванова Айгерим<\/button>/);
});

test('не начинавшие смену сворачиваются в строку с числом; со статусом или чатами — на виду', () => {
    const html = render([
        person({ id: 1, user_id: 1, name: 'Иванова Айгерим', status_key: 'free', status_label: 'Активный', status_weight: 20 }),
        person({ id: 2, user_id: 2, name: 'Петров Данияр' }),                         // нет событий, чатов нет
        person({ id: 3, user_id: 3, name: 'Сидорова Дана', chats: 2 }),              // событий нет, но писала
        person({ id: 4, user_id: 4, name: 'Абаев Ерлан', status_key: 'offline', status_label: 'Не в сети', status_weight: 70 }),
    ]);
    assert.ok(!html.includes('>Петров Данияр<'));
    for (const name of ['Иванова Айгерим', 'Сидорова Дана', 'Абаев Ерлан']) assert.ok(html.includes(`>${name}<`), name);
    assert.match(html, /Показать не начинавших смену<span class="tabular-nums text-slate-400">1<\/span>/);
    // У «Нет событий» журнала нет: строка не нажимается.
    assert.ok(!/title="Журнал статусов"[^>]*>Сидорова Дана</.test(html));
    assert.match(html, /<span class="font-medium">Сидорова Дана<\/span>/);
});

test('пустые состояния говорят словами', () => {
    assert.match(render([]), /В группе верификаторов нет сотрудников\./);
    const idle = render([person({})]);
    assert.match(idle, /Сегодня никто из верификаторов не начинал смену\./);
    assert.match(idle, /не начинавших смену/);
});
