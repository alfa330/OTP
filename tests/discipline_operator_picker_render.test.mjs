/*
 * «Дисциплина»: выбор сотрудника в форме нарушения не двигает окно.
 *
 * Жалоба владельца 22.10.2026 на «пролаги» формы «Зафиксировать нарушение»
 * (скриншот на большом экране). Замер на стенде, 1680 px:
 * - лупа лежала на первых буквах «ФИО или логин»: у поля на ступени 2xl отступ
 *   16 px вместо 36 — `2xl:px-4` из dInput перебивал простой `pl-9`;
 * - окно открывалось ростом 316 px со строкой «Начните вводить ФИО» и через
 *   0,2 с за один кадр становилось 547 px; пока набирали ФИО, 40 строк
 *   сменялись одной — окно теряло 237 px, и поле ввода уезжало на ~120 px;
 * - в углах шапки и подвала окна проступали бледные дуги: их backdrop-blur у
 *   скругления захватывал затемнённую страницу снаружи окна.
 *
 * Серверный рендер (react-dom/server) эффектов не запускает: компонент
 * остаётся в том состоянии, в каком его видит человек в первый кадр после
 * щелчка, — запрос ещё не ушёл. Его и проверяем, плюс каждое состояние списка.
 *
 * Запуск: node --test tests/discipline_operator_picker_render.test.mjs
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
// Сборка — внутри node_modules: из системного temp react и lucide-react не резолвятся.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-discipline-picker-test/', import.meta.url)
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

const { default: RecordForm } = await bundle('src/components/discipline/RecordForm.jsx', 'RecordForm');
const { OperatorPicker } = await bundle('src/components/discipline/OperatorPicker.jsx', 'OperatorPicker');
const { IosModal } = await bundle('src/components/ui/ios.jsx', 'ios');

const never = () => new Promise(() => {});
const api = { searchOperators: never, operatorContext: never, createRecord: never };
const PEOPLE = [
    { id: 1, name: 'Абдеш Мирас Ерболулы', group_name: 'Полатжанкызы Жанис группа Основа', department_name: 'СЗоВ', role: 'operator' },
    { id: 2, name: 'Абдикасимова Нилуфар', group_name: 'Сабыр Азана группа Чат менеджер', department_name: 'СЗоВ', role: 'trainee' },
];

// Блок списка: обёртка с прокруткой сразу после поля поиска.
const listBox = (html) => {
    const match = html.match(/<div class="(thin-scroll overflow-y-auto[^"]*)">/);
    assert.ok(match, 'нет блока списка');
    return match[1].split(/\s+/);
};
const searchInput = (html) => {
    const match = html.match(/<input[^>]*placeholder="ФИО или логин"[^>]*>/);
    assert.ok(match, 'нет поля поиска');
    return match[0];
};

const picker = (props) => renderToStaticMarkup(React.createElement(OperatorPicker, {
    query: '', onQuery: () => {}, results: [], loaded: false, searching: false, failed: false, onPick: () => {}, ...props,
}));

test('первый кадр формы: заготовка списка в полный рост, а не «Начните вводить ФИО»', () => {
    const html = renderToStaticMarkup(React.createElement(RecordForm, {
        open: true, api, toast: () => {}, userId: 7, onClose: () => {},
    }));
    assert.ok(html.includes('Зафиксировать нарушение'));
    assert.ok(!html.includes('Начните вводить ФИО'), 'короткая строка вместо списка — окно вырастет на ответе');
    const box = listBox(html);
    assert.ok(box.includes('h-[300px]'), 'высота списка не закреплена');
    assert.ok(!box.some((cls) => cls.startsWith('max-h-')), 'max-h: окно растёт и сжимается вместе со списком');
    assert.match(html, /<ul class="divide-y divide-slate-100" aria-busy="true"/);
    assert.equal((html.match(/animate-pulse rounded-full bg-slate-200\/70/g) || []).length, 10, 'пять строк заготовки по две полосы');
});

test('лупа не ложится на текст и на большом экране', () => {
    const html = picker({ loaded: true, results: PEOPLE });
    const input = searchInput(html);
    const cls = input.match(/class="([^"]*)"/)[1].split(/\s+/);
    // dInput на 2xl ставит px-4 (16 px) — без своего 2xl:pl-* он перебивает pl-9.
    assert.ok(cls.includes('2xl:px-4'), 'поле перестало быть dInput — проверка устарела');
    assert.ok(cls.includes('pl-9') && cls.includes('2xl:pl-12'), 'нет отступа под лупу на ступени 2xl');
    const icon = html.match(/<svg[^>]*class="([^"]*lucide-search[^"]*)"/);
    assert.ok(icon, 'нет лупы');
    assert.ok(icon[1].includes('2xl:left-4') && icon[1].includes('2xl:size-[18px]'), 'лупа не под ступень 2xl');
});

test('во всех состояниях список одной высоты — окно стоит', () => {
    const states = {
        loading: { loaded: false },
        rows: { loaded: true, results: PEOPLE },
        searching: { loaded: true, results: [], searching: true, query: 'Абд' },
        nothing: { loaded: true, results: [], query: 'zzz' },
        empty: { loaded: true, results: [] },
        failed: { loaded: true, results: [], failed: true },
    };
    const boxes = Object.fromEntries(Object.entries(states).map(([key, props]) => [key, listBox(picker(props)).join(' ')]));
    assert.equal(new Set(Object.values(boxes)).size, 1, JSON.stringify(boxes));
    assert.ok(boxes.loading.includes('h-[300px]'));
    // Своя высота у окна особого контроля — та же во всех состояниях.
    assert.ok(listBox(picker({ loaded: true, results: PEOPLE, listClassName: 'h-[220px]' })).includes('h-[220px]'));
});

test('что стоит в списке в каждом состоянии', () => {
    const rows = picker({ loaded: true, results: PEOPLE });
    assert.ok(rows.includes('Абдеш Мирас Ерболулы') && rows.includes('Абдикасимова Нилуфар'));
    assert.ok(rows.includes('Сабыр Азана группа Чат менеджер · СЗоВ · стажёр'));
    assert.ok(!rows.includes('aria-busy'));
    // Пока ищется новая строка, прежние результаты стоят на месте.
    assert.ok(picker({ loaded: true, results: PEOPLE, searching: true }).includes('Абдеш Мирас Ерболулы'));
    assert.ok(picker({ loaded: true, results: [], searching: true, query: 'Абд' }).includes('aria-label="Загрузка"'));
    assert.ok(picker({ loaded: true, results: [], query: 'zzz' }).includes('Никого не нашли среди ваших сотрудников'));
    assert.ok(picker({ loaded: true, results: [] }).includes('Среди ваших сотрудников пока никого нет'));
    assert.ok(picker({ loaded: true, results: [], failed: true, query: 'Абд' }).includes('Список не загрузился'));
});

test('шапка и подвал окна без backdrop-blur — размывать под ними нечего, а в углах были дуги', () => {
    const html = renderToStaticMarkup(React.createElement(IosModal, {
        open: true, title: 'Зафиксировать нарушение', onClose: () => {},
        footer: React.createElement('button', { type: 'button' }, 'Зафиксировать'),
    }, React.createElement('p', null, 'тело')));
    const panel = html.slice(html.indexOf('otp-modal-panel'));
    assert.ok(html.includes('otp-modal-head'), 'нет шапки окна');
    assert.ok(panel.includes('Зафиксировать</button>'), 'нет подвала окна');
    assert.ok(!/backdrop-blur/.test(panel), 'размытие внутри окна вернулось');
    // Затемнение за окном — по-прежнему с размытием страницы.
    assert.ok(/otp-modal-dim[^"]*backdrop-blur-md/.test(html));
});
