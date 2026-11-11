/*
 * Непустые «Касания» должны рендериться после загрузки CDR и передавать PBX
 * до кнопки перезвона. SSR не запускает эффекты, поэтому при сборке подставляем
 * только начальные ответы CDR/PBX; компоненты и usePbxLive остаются настоящими.
 * Запуск: node --test tests/cdr_touches_render.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const esbuild = require('esbuild');
const ROOT = fileURLToPath(new URL('../', import.meta.url));
const SEED = 'const [data, setData] = useState(null);';
const touch = {
    linkedid: 'regression-call', phone: '7011234567', line_number: '7271234567',
    started_at: '2026-10-22T10:00:00', operator: 'Тестовый оператор', ext: '6101',
    call_type: 'Исходящий', result: 'Разговор', talk_seconds: 42,
};
const fixture = { touches: { touches: [touch], total: 1 }, live: null };

const result = await esbuild.build({
    stdin: {
        contents: `
            import React from 'react';
            import { renderToStaticMarkup } from 'react-dom/server';
            import TouchesView, { ReportsModal, TouchesTable } from './src/components/cdr/TouchesView.jsx';
            export const renderView = (props) => renderToStaticMarkup(React.createElement(TouchesView, props));
            export const renderTable = (props) => renderToStaticMarkup(React.createElement(TouchesTable, props));
            export const renderReports = (props) => renderToStaticMarkup(React.createElement(ReportsModal, props));
        `,
        resolveDir: ROOT, loader: 'jsx',
    },
    bundle: true, write: false, format: 'cjs', platform: 'node', target: 'node18',
    jsx: 'transform', loader: { '.jsx': 'jsx', '.js': 'jsx' },
    external: ['react', 'react-dom', 'lucide-react'], logLevel: 'silent',
    plugins: [{
        name: 'seed-touches-responses',
        setup(build) {
            build.onResolve({ filter: /^axios$/ }, () => ({ path: 'axios', namespace: 'stub' }));
            build.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({
                contents: 'export default { get() { throw new Error("Unexpected network request"); } };',
            }));
            build.onLoad({ filter: /(?:TouchesView|LivePanel)\.jsx$/ }, ({ path }) => {
                const source = readFileSync(path, 'utf8');
                const isView = path.endsWith('TouchesView.jsx');
                assert.ok(source.includes(SEED), 'изменилось начальное состояние data — обновите подмену');
                return {
                    contents: source.replace(SEED,
                        `const [data, setData] = useState(__TOUCHES_TEST__.${isView ? 'touches' : 'live'});`)
                        // Вспомогательную таблицу открываем только в тестовой сборке.
                        + (isView ? '\nexport { TouchesTable };' : ''),
                    loader: 'jsx',
                };
            });
        },
    }],
});
const module = { exports: {} };
new Function('module', 'exports', 'require', '__TOUCHES_TEST__', result.outputFiles[0].text)
    (module, module.exports, require, fixture);
const { renderView, renderTable, renderReports } = module.exports;

function tableMarkup(html) {
    const table = html.match(/<table\b[\s\S]*?<\/table>/)?.[0];
    assert.ok(table, 'отрисована таблица, а не начальный экран загрузки');
    assert.match(table, /<tbody[\s\S]*Тестовый оператор/);
    assert.match(table, /\+7 701 123 45 67/);
    return table;
}

for (const [name, live, canCall] of [
    ['с правом звонить', { me: { can_call: true, ext: '6101' }, calls: [], lines: [] }, true],
    ['без права звонить', { me: { can_call: false }, calls: [], lines: [] }, false],
    ['до ответа PBX', null, false],
]) {
    test(`TouchesView: непустая таблица ${name}`, () => {
        fixture.live = live;
        const table = tableMarkup(renderView({ apiBaseUrl: 'http://api.test' }));
        assert.equal(table.includes('aria-label="Позвонить"'), canCall);
    });
}

test('TouchesView: файлы раздела — за одной кнопкой «Отчёты»', () => {
    fixture.live = null;
    const html = renderView({ apiBaseUrl: 'http://api.test' });
    assert.match(html, /<button[^>]*>(?:<svg[\s\S]*?<\/svg>)?Отчёты<\/button>/);
    // Прежней отдельной кнопки выгрузки нет: она стала первым отчётом в окне.
    assert.doesNotMatch(html, /Выгрузить в Excel/);
});

/* Окно «Отчёты»: два отчёта с описанием, у каждого своя «Скачать». */
const reportsMarkup = (props) => renderReports({
    open: true, onClose: () => {}, onDownload: () => {},
    range: { from: '2026-11-02', to: '2026-11-10' }, total: 1840, filtersCount: 0, downloading: '',
    ...props,
});
// Кнопка целиком, не захватывая соседние; «выключена» — атрибут, а не класс disabled:opacity-50.
const downloadButtons = (html) => html.match(/<button(?:(?!<button)[\s\S])*?(?:Скачать|Готовим…)<\/button>/g) || [];
const isOff = (button) => / disabled=""/.test(button);

test('ReportsModal: оба отчёта с описанием и периодом раздела', () => {
    const html = reportsMarkup();
    assert.match(html, /<h3[^>]*>Отчёты<\/h3>/);
    // Период не выбирается заново — назван тот, что стоит на экране раздела.
    assert.match(html, /Период: /);
    assert.match(html, />Касания<\/div>/);
    assert.match(html, />Отчёт по входу<\/div>/);
    assert.match(html, /без автообзвона/);
    const buttons = downloadButtons(html);
    assert.equal(buttons.length, 2);
    for (const button of buttons) assert.equal(isOff(button), false);
    // Подписей-оговорок нет, пока говорить не о чем.
    assert.doesNotMatch(html, /не действуют|ничего не попало/);
});

test('ReportsModal: пустую выгрузку не дать, а «Отчёт по входу» от фильтров не зависит', () => {
    const html = reportsMarkup({ total: 0, filtersCount: 2 });
    const [touchesButton, inboundButton] = downloadButtons(html);
    assert.equal(isOff(touchesButton), true);
    assert.equal(isOff(inboundButton), false);
    assert.match(html, /За этот период под фильтры ничего не попало\./);
    assert.match(html, /Фильтры раздела на этот отчёт не действуют\./);
});

test('ReportsModal: пока собирается один файл, второй не начать', () => {
    const [touchesButton, inboundButton] = downloadButtons(reportsMarkup({ downloading: 'inbound' }));
    assert.match(touchesButton, /Скачать/);
    assert.equal(isOff(touchesButton), true);
    assert.match(inboundButton, /Готовим…/);
    assert.equal(isOff(inboundButton), true);
});

test('TouchesTable: отсутствие pbx не мешает показать касания', () => {
    const table = tableMarkup(renderTable({
        touches: [touch], total: 1, page: 1, pageCount: 1, onPage: () => {},
    }));
    assert.doesNotMatch(table, /aria-label="Позвонить"/);
});
