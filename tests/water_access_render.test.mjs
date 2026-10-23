/*
 * «Учёт воды» → «Настройки» → «Доступ по офисам» (владелец 23.10.2026: «выбираем
 * офис, далее из фронт-офисников выбираем людей, которым открыт данный раздел,
 * и выдаём доступ»).
 *
 * Серверный рендер (react-dom/server) эффектов не запускает — это первый кадр,
 * который человек видит после щелчка. Окно обязано открыться сразу с отметками
 * сохранённого доступа: черновик заполняется в той же отрисовке, а не эффектом
 * после неё (иначе кадр стоял бы пустым, а потом галочки «прыгали» на место).
 *
 * Запуск: node --test tests/water_access_render.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
// Сборка — внутри node_modules: из системного temp react и lucide-react не резолвятся.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-water-access-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

const AXIOS_STUB = `
  const never = () => new Promise(() => {});
  export default Object.assign(never, { get: never, post: never, patch: never, delete: never });
`;

async function bundle(entry, name) {
    const stubPath = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stubPath, AXIOS_STUB);
    const result = await esbuild.build({
        entryPoints: [new URL(entry, ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
        bundle: true,
        write: false,
        format: 'esm',
        platform: 'node',
        target: 'node18',
        jsx: 'transform',
        loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty' },
        external: ['react', 'react-dom', 'lucide-react'],
        alias: { axios: stubPath },
        logLevel: 'silent',
    });
    const outPath = join(OUT_DIR, `${name}.mjs`);
    writeFileSync(outPath, result.outputFiles[0].text);
    return import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
}

const { default: WaterAccess, WaterAccessSheet } = await bundle('src/components/water/WaterAccess.jsx', 'WaterAccess');
const { default: WaterIssue } = await bundle('src/components/water/WaterIssue.jsx', 'WaterIssue');

const office = (id, city, name, address, userIds, changed = null) => ({ id, city, name, address, user_ids: userIds, changed });
const ACCESS = {
    offices: [
        office(1, 'Алматы', 'Офис для подключения тарифа «Бизнес»', 'Байзакова 78А', [11, 12],
            { by_name: null, at: '2026-10-23T18:00:00', by_seed: true }),
        office(2, 'Астана', 'Офис Астана', 'Сарыарка 31', [21],
            { by_name: 'Руководитель', at: '2026-10-23T18:05:00', by_seed: false }),
    ],
    people: [
        { id: 21, name: 'Борисов Борис', city: 'Астана' },
        { id: 12, name: 'Яковлев Яков', city: 'Алматы' },
        { id: 11, name: 'Алиев Али', city: 'Алматы' },
        { id: 13, name: 'Ветров Виктор', city: 'Алматы' },
    ],
};

const sheet = (props) => renderToStaticMarkup(React.createElement(WaterAccessSheet, {
    open: true, onClose: () => {}, access: ACCESS, onSaved: () => {},
    apiBaseUrl: 'http://api.test', headers: () => ({}), showToast: () => {}, ...props,
}));

const rows = (html) => [...html.matchAll(/<button[^>]*role="checkbox"[^>]*aria-checked="(true|false)"[^>]*>([\s\S]*?)<\/button>/g)]
    .map((match) => ({ checked: match[1] === 'true', text: match[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim() }));
const groupTitles = (html) => [...html.matchAll(/<div role="group" aria-label="([^"]+)"/g)].map((match) => match[1]);
const stampLine = (html) => html.match(/<p class="px-1 text-\[11\.5px\][^"]*">([^<]*)<\/p>/)?.[1];

test('окно открывается сразу с отметками сохранённого доступа', () => {
    const html = sheet();
    assert.match(html, /Доступ к выдаче/);
    // Первый офис — Алматы: его город — первой группой, отмечены выдающие там.
    // Город назван подписью группы один раз, а не в каждой строке.
    assert.deepEqual(groupTitles(html), ['Сотрудники · Алматы', 'Сотрудники · Астана']);
    assert.deepEqual(rows(html), [
        { checked: true, text: 'Алиев Али' },
        { checked: false, text: 'Ветров Виктор' },
        { checked: true, text: 'Яковлев Яков' },
        { checked: false, text: 'Борисов Борис' },
    ]);
    // Полное ФИО — в title: длинное обрезается.
    assert.match(html, /role="checkbox" aria-checked="true" title="Алиев Али"/);
    // Подзаголовок — где офис: по адресу его и узнают за стойкой.
    assert.match(html, /Алматы · Байзакова 78А/);
    // Доступ выдан переходом, а не человеком — окно так и говорит.
    assert.match(html, /Выдано по городу сотрудника · 23\.10\.2026/);
});

test('два-три офиса — сегментами с числом выдающих, больше — списком', () => {
    const html = sheet();
    const tabs = [...html.matchAll(/<button[^>]*role="tab"[^>]*aria-selected="(true|false)"[^>]*>([\s\S]*?)<\/button>/g)]
        .map((match) => [match[1] === 'true', match[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()]);
    assert.deepEqual(tabs, [[true, 'Алматы 2'], [false, 'Астана 1']]);

    const many = { ...ACCESS, offices: [
        ...ACCESS.offices,
        office(3, 'Алматы', 'Офис Алматы №1', 'Жамбыла 172В', []),
        office(4, 'Алматы', 'Офис Алматы №2', '7 мкр 5', []),
    ] };
    const listed = sheet({ access: many });
    assert.doesNotMatch(listed, /role="tablist"/);
    assert.match(listed, /aria-label="Офис"/);
});

test('выбранная строка — мягко-голубая с галочкой и своим разделителем', () => {
    const html = sheet();
    const checked = html.match(/<button[^>]*aria-checked="true"[^>]*>/)[0];
    assert.match(checked, /bg-blue-50/);
    // Вторая отмеченная строка подряд — с голубой линией над собой: иначе
    // отмеченные сливаются в один голубой блок.
    const second = [...html.matchAll(/<button[^>]*role="checkbox"[^>]*>/g)][2][0];
    assert.match(second, /border-t border-blue-100 bg-blue-50/);
    assert.doesNotMatch(html, /bg-slate-900(?!\/)/);  // затемнение окна bg-slate-900/40 — не выбор
    // «Сохранить» спит, пока ничего не переключено; «Отменить» нет вовсе.
    assert.match(html, /<button[^>]*disabled=""[^>]*>Сохранить<\/button>/);
    assert.doesNotMatch(html, />Отменить</);
});

test('строка «кто менял» стоит и у офиса, где доступ ещё не меняли', () => {
    // Иначе при смене офиса окно прыгало на её высоту (разбор 23.10.2026).
    const fresh = { ...ACCESS, offices: [{ ...ACCESS.offices[1], changed: null }, ACCESS.offices[0]] };
    assert.equal(stampLine(sheet({ access: fresh })), '\u00a0');
    assert.match(stampLine(sheet()), /^Выдано по городу сотрудника · /);
});

test('два офиса одного города — списком, а не сегментами', () => {
    const twins = { ...ACCESS, offices: [
        ...ACCESS.offices,
        office(3, 'Алматы', 'Офис Алматы №1', 'Улица Жамбыла, 172В угол улицы Байзакова', []),
    ] };
    const html = sheet({ access: twins });
    assert.doesNotMatch(html, /role="tablist"/);
    assert.match(html, /aria-haspopup="listbox"[^>]*>|aria-label="Офис"/);
});

test('пустые состояния говорят, что делать', () => {
    assert.match(sheet({ access: { offices: [], people: [] } }),
        /Офисов в учёте пока нет — добавьте их во вкладке «Остатки»/);
    assert.match(sheet({ access: { ...ACCESS, people: [] } }),
        /Сотрудников фронт-офисов, которым открыт раздел, пока нет/);
});

test('закрытое окно не рисуется, строка «Настроек» ждёт ответа своего роста', () => {
    assert.equal(sheet({ open: false }), '');
    const html = renderToStaticMarkup(React.createElement(WaterAccess, {
        apiBaseUrl: 'http://api.test', headers: () => ({}), showToast: () => {},
    }));
    assert.match(html, /Выдача воды/);
    assert.match(html, /Доступ по офисам/);
    // До ответа — неразрывный пробел, а не «Загружаем…»: строка не перестроится.
    assert.match(html, /<span class="block truncate[^"]*">\u00a0<\/span>/);
    assert.match(html, /<button[^>]*disabled=""/);
    // Столбик на gap, а не на space-y: окно — его ребёнок.
    assert.match(html, /^<section class="flex flex-col gap-1\.5/);
});

/* ── «Выдача» офисника без единого открытого офиса ─────────────────────── */

const issueScreen = (props) => renderToStaticMarkup(React.createElement(WaterIssue, {
    apiBaseUrl: 'http://api.test', headers: () => ({}), settings: {},
    capabilities: { can_issue: true, issue_office_ids: [], default_city: 'Алматы' },
    offices: [{ id: 1, is_active: true, city: 'Алматы', name: 'Офис', stock: 10 }],
    onOfficeUpdated: () => {}, onCapabilities: () => {}, showToast: () => {}, ...props,
}));

test('офисник без офисов проверяет водителя и знает, у кого просить доступ', () => {
    const html = issueScreen();
    assert.match(html, /Выдавать воду вам пока не открыто ни в одном офисе — доступ выдаёт руководитель/);
    const input = html.match(/<input[^>]*placeholder="Ссылка на водителя[^>]*>/)[0];
    assert.doesNotMatch(input, /disabled/);
    // Списка офисов нет вовсе — выбирать нечего.
    assert.doesNotMatch(html, /aria-label="Офис"/);
});

test('офисник с открытым офисом без выбора офиса водителя не вводит', () => {
    const html = issueScreen({ capabilities: { can_issue: true, issue_office_ids: [1], default_city: 'Алматы' } });
    assert.doesNotMatch(html, /Выдавать воду вам пока не открыто/);
    const input = html.match(/<input[^>]*placeholder="Ссылка на водителя[^>]*>/)[0];
    assert.match(input, /disabled/);
});

test('отказ «не ваш офис» применяет свежие права и не проверяет водителя по отнятому офису', () => {
    // Блок catch выдачи из исходника, исполненный с подменами: порядок «права →
    // выход» держит офис вне списка, перепроверки по нему нет.
    const source = readFileSync(new URL('src/components/water/WaterIssue.jsx', ROOT), 'utf8');
    const issue = source.slice(source.indexOf('const issue = async () => {'));
    const body = issue.slice(issue.indexOf('} catch (requestError) {') + '} catch (requestError) {'.length,
        issue.indexOf('} finally {'));
    const run = new Function('requestError', 'onCapabilities', 'toastRef', 'ticket', 'myTicket',
        'setResult', 'setBlocks', 'check', 'office', 'target', body);
    const fresh = { can_issue: true, issue_office_ids: [] };
    const calls = [];
    run({ response: { data: { code: 'WATER_OFFICE_FORBIDDEN', capabilities: fresh, error: 'нельзя' } } },
        (caps) => calls.push(['caps', caps]), { current: (text) => calls.push(['toast', text]) }, { current: 1 }, 1,
        () => calls.push(['result']), () => {}, () => calls.push(['check']), { id: 2 }, 'acc');
    assert.deepEqual(calls, [['toast', 'нельзя'], ['caps', fresh]]);
    // Другой отказ без вердикта — по-прежнему перепроверка водителя.
    calls.length = 0;
    run({ response: { data: { code: 'WATER_NOT_ELIGIBLE', error: 'нет' } } },
        (caps) => calls.push(['caps', caps]), { current: () => {} }, { current: 1 }, 1,
        () => calls.push(['result']), () => {}, () => calls.push(['check']), { id: 2 }, 'acc');
    assert.deepEqual(calls, [['check']]);
});
