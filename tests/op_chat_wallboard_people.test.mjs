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
            import OpChatWallboardBody, { VerifiersTable } from './src/components/monitoring/OpChatWallboard.jsx';
            export const renderTable = (props) => renderToStaticMarkup(React.createElement(VerifiersTable, props));
            export const renderBody = (props) => renderToStaticMarkup(React.createElement(OpChatWallboardBody, props));
            export { OP_CHAT_METRICS, OP_CHAT_METRIC_MAP, DEFAULT_OP_CHAT_WIDGET_METRICS } from './src/components/monitoring/opWallboardShared.js';
            export { sanitizeWidgetMetrics } from './src/components/monitoring/szovWallboardShared.js';`,
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
const {
    renderTable, renderBody, OP_CHAT_METRICS, OP_CHAT_METRIC_MAP, DEFAULT_OP_CHAT_WIDGET_METRICS, sanitizeWidgetMetrics,
} = await import(pathToFileURL(output));

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

test('Syntony: статус по ответам — без журнала, свёрнуты «не отвечавшие»', () => {
    // Снимок Syntony (26.11.2026): статус по ответам клиенту за 15 минут, журнала смен у него нет.
    const html = renderTable({
        snapshot: { day: '2026-11-26', statuses_source: 'replies', journal: false, now: { operators: [
            person({ id: 1, user_id: 1, name: 'Иванова Айгерим', status_key: 'free', status_label: 'Онлайн',
                status_weight: 20, status_seconds: 540, entry_at: '2026-11-26T09:00:00', in_work: 1, chats: 4 }),
            person({ id: 2, user_id: 2, name: 'Петров Данияр', status_key: 'offline', status_label: 'Не в системе',
                status_weight: 70, status_seconds: 6300, entry_at: '2026-11-26T08:10:00', chats: 6 }),
            person({ id: 3, user_id: 3, name: 'Сидорова Дана', status_label: 'Нет ответов' }),
        ] } },
        firstTarget: 60, innerTarget: 240, onOpenJournal: () => {},
    });
    assert.deepEqual(cells(html, 'Иванова Айгерим').slice(0, 3), ['Иванова Айгерим', 'Онлайн9:00', '09:00']);
    assert.deepEqual(cells(html, 'Петров Данияр').slice(1, 2), ['Не в системе1:45:00']);
    assert.ok(!html.includes('title="Журнал статусов"'));            // ФИО не нажимается
    assert.ok(!html.includes('fa-chevron-right'));
    assert.match(html, /Показать не отвечавших<span class="tabular-nums text-slate-400">1<\/span>/);
    const nobody = renderTable({ snapshot: { day: '2026-11-26', statuses_source: 'replies', journal: false,
        now: { operators: [person({})] } }, firstTarget: 60, innerTarget: 240 });
    assert.match(nobody, /Никто из верификаторов не отвечал клиентам\./);
});

test('«Чаты · сейчас» — три плитки, «Ждёт дольше всех» убран (владелец 27.11.2026)', () => {
    const html = renderBody({ snapshot: { day: '2026-11-27', now: {
        chats_in_work: 19, chats_waiting: 12, longest_wait_seconds: 3570, verifiers_in_work: 2, operators: [],
    }, today: { chats: 211 } } });
    const now = html.slice(0, html.indexOf('Показатели за день'));
    const labels = [...now.matchAll(/<div class="text-\[15px\] font-semibold[^"]*">([^<]+)<\/div>/g)]
        .map((match) => match[1]);
    assert.deepEqual(labels, ['Чатов в работе', 'Ждут ответа', 'Верификаторов в работе']);
    assert.match(now, /lg:grid-cols-3/);                     // ряд по колонкам «Показателей за день»
    assert.ok(!html.includes('Ждёт дольше всех'));
    assert.ok(!html.includes('59:30'));                      // 3570 с нигде не всплыли
});

test('виджет «ОП · Чат»: «Ждёт дольше всех» нет в выборе, сохранённый набор теряет только его (владелец 27.11.2026)', () => {
    assert.ok(!OP_CHAT_METRICS.some((metric) => metric.key === 'op_chat_longest_wait' || metric.label === 'Ждёт дольше всех'));
    assert.deepEqual(OP_CHAT_METRICS.filter((metric) => metric.group === 'now').map((metric) => metric.label),
        ['Чатов в работе', 'Ждут ответа', 'Верификаторов в работе']);
    // Браузер, где показатель был в наборе: он выпадает, остальное на месте.
    assert.deepEqual(sanitizeWidgetMetrics(['op_chat_in_work', 'op_chat_longest_wait', 'op_chat_first'], OP_CHAT_METRIC_MAP),
        ['op_chat_in_work', 'op_chat_first']);
    // Был выбран только он — набор пуст, и виджет откроется показателями по умолчанию.
    assert.deepEqual(sanitizeWidgetMetrics(['op_chat_longest_wait'], OP_CHAT_METRIC_MAP), []);
    assert.ok(DEFAULT_OP_CHAT_WIDGET_METRICS.every((key) => OP_CHAT_METRIC_MAP[key]));
});
