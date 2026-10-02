/*
 * Табло СЗоВ, направление «Чат»: колонка людей и плитка «Не в системе» в настоящей отрисовке.
 * Браузера в проекте нет, поэтому компонент собирается esbuild'ом в один модуль и рисуется
 * через react-dom/server — так проверяется разметка, а не строчки исходника: какая вкладка
 * открыта, чей список под ней, какие подписи у людей и где стоит число. Графики recharts
 * подменены пустышками: в серверном рендере им нечего рисовать, а проверяем мы не их.
 * Имена вымышленные — репозиторий публичный.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешний react не резолвится.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-szov-chat-render-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

const RECHARTS_STUB = ['Bar', 'CartesianGrid', 'ComposedChart', 'LabelList', 'Legend', 'Line',
    'ReferenceLine', 'ResponsiveContainer', 'Tooltip', 'XAxis', 'YAxis']
    .map((name) => `export const ${name} = () => null;`).join('\n');

async function bundle() {
    const stubPath = join(OUT_DIR, 'recharts-stub.mjs');
    writeFileSync(stubPath, RECHARTS_STUB);
    const result = await esbuild.build({
        entryPoints: [new URL('src/components/monitoring/SzovChatWallboard.jsx', ROOT).pathname
            .replace(/^\/([A-Za-z]:)/, '$1')],
        bundle: true,
        write: false,
        format: 'esm',
        platform: 'node',
        target: 'node18',
        jsx: 'transform',
        loader: { '.jsx': 'jsx', '.js': 'jsx' },
        external: ['react', 'react-dom', 'lucide-react'],
        alias: { recharts: stubPath },
        logLevel: 'silent',
    });
    const outPath = join(OUT_DIR, 'szov-chat-wallboard.mjs');
    writeFileSync(outPath, result.outputFiles[0].text);
    return import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
}

const { default: SzovChatWallboardBody, ChatPeopleColumn } = await bundle();

const ON_SHIFT = [
    { operator_id: 235, name: 'Тестова Алия Тестовна', status: 'Онлайн', status_key: 'online',
      since: '2026-10-02 09:00:00', seconds: 3600, open_chats: 7, chats: 12,
      first_reply_seconds: 40, inner_reply_seconds: 90 },
];
const OFFLINE = [
    { operator_id: 18, name: 'Ночная Дана Сменовна', since: '2026-10-02 18:59:52' },
    { operator_id: 149, name: 'Примеров Бекзат Примерулы', since: '2026-10-01 22:09:27' },
    { operator_id: 37, name: 'Учебный Ерлан Тренингулы', since: '' },
];
const SNAPSHOT = {
    day: '2026-10-02',
    target_seconds: 120,
    now: { operators_online: 1, operators_busy: 0, operators_on_training: 0, operators_offline: 3,
           open_chats: 7, operators: ON_SHIFT, offline_operators: OFFLINE },
    today: { chats: 40, first_reply_seconds: 40, inner_reply_seconds: 90 },
    hourly: [],
};

const tabs = (html) => [...html.matchAll(/<button[^>]*role="tab"[^>]*aria-selected="(true|false)"[^>]*>(.*?)<\/button>/g)]
    .map(([, selected, inner]) => `${inner.replace(/<[^>]+>/g, '')}:${selected}`);

const column = (page, extra = {}) => renderToStaticMarkup(React.createElement(ChatPeopleColumn, {
    people: ON_SHIFT, offlinePeople: OFFLINE, day: '2026-10-02', targetSeconds: 120,
    page, onPageChange: () => {}, ...extra,
}));

test('табло: по умолчанию открыта смена, число вышедших — на вкладке и плиткой', () => {
    const html = renderToStaticMarkup(React.createElement(SzovChatWallboardBody, { snapshot: SNAPSHOT }));
    assert.deepEqual(tabs(html), ['На смене1:true', 'Не в системе3:false']);
    assert.match(html, /Тестова Алия Тестовна/);
    assert.doesNotMatch(html, /Ночная Дана Сменовна/);
    // Плитка «Не в системе» — кнопка с числом рядом с остальными людьми, до «Открыто чатов».
    const tile = html.match(/<button type="button" title="Показать, кто не в системе"[^>]*>(.*?)<\/button>/);
    assert.ok(tile, 'плитка «Не в системе» должна быть кнопкой');
    assert.match(tile[1], />Не в системе</);
    assert.match(tile[1], />3</);
    assert.ok(html.indexOf('Показать, кто не в системе') < html.indexOf('Открыто чатов'));
    // Прежней строки «Не в системе: N» внизу колонки больше нет.
    assert.doesNotMatch(html, /Не в системе: /);
});

test('вкладка «Не в системе»: вышедшие с моментом выхода, смены под ней нет', () => {
    const html = column('offline');
    assert.deepEqual(tabs(html), ['На смене1:false', 'Не в системе3:true']);
    assert.doesNotMatch(html, /Тестова Алия Тестовна/);
    const people = [...html.matchAll(/<li[^>]*>(.*?)<\/li>/g)].map(([, inner]) => inner.replace(/<[^>]+>/g, '|')
        .split('|').filter(Boolean).join(' · '));
    // Порядок задаёт сервер; подпись — время сегодня, дата раньше, без момента — ничего.
    assert.deepEqual(people, [
        'Ночная Дана Сменовна · с 18:59',
        'Примеров Бекзат Примерулы · с 01.10',
        'Учебный Ерлан Тренингулы',
    ]);
});

test('вкладка «На смене»: смена со статусами, вышедших под ней нет', () => {
    const html = column('shift');
    assert.deepEqual(tabs(html), ['На смене1:true', 'Не в системе3:false']);
    assert.match(html, /Тестова Алия Тестовна/);
    assert.match(html, />Онлайн</);
    assert.doesNotMatch(html, /Ночная Дана Сменовна/);
});

test('никого не в системе — «Никого», а не пустая страница', () => {
    const html = column('offline', { offlinePeople: [] });
    assert.match(html, />Никого</);
    assert.doesNotMatch(html, /<li/);
});
