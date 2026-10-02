/*
 * Справочник в поиске вики: офисы и комиссии Яндекса (searchDirectory.jsx,
 * сервер — wiki/directory.py).
 *
 * Что стережётся:
 *   1. ПОРЯДОК СТРОК. Справочник — первые строки списка, по которому ходят
 *      стрелки; помощник остаётся последним, прежние вызовы searchRows без
 *      третьего аргумента не меняются.
 *   2. ДВЕРЬ ТОЛЬКО ТУДА, ГДЕ ВКЛАДКА ЕСТЬ. Строка офиса без вкладки «Офисы»
 *      вела бы в никуда — её не рисуем.
 *   3. ПУСТОТА. Нашёлся один справочник — это ответ, а не «Ничего не найдено».
 *   4. ИСТОЧНИК ПОМОЩНИКА. Чип справочника без slug — рабочая кнопка во
 *      вкладку, а не серая плашка «без статьи».
 *
 * JSX здесь не используется намеренно: node --test гоняет .mjs без сборки.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');
const { mkdirSync } = require('node:fs');
const { join } = require('node:path');
const { fileURLToPath } = require('node:url');

async function load(name, file) {
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, `${name}.mjs`);
  buildSync({
    entryPoints: [fileURLToPath(new URL(file, import.meta.url))],
    bundle: true,
    format: 'esm',
    target: 'node18',
    outfile,
    external: ['react', 'react-dom', 'axios', 'lucide-react', 'framer-motion'],
    loader: { '.jsx': 'jsx' },
    logLevel: 'silent',
  });
  return import(`file://${outfile.replace(/\\/g, '/')}`);
}

const { directoryKey, directoryText, openableDirectory, directoryTab } = await load(
  'searchDirectory', '../src/components/wiki/searchDirectory.jsx');
const { ResultsPane, searchRows } = await load('WikiSearchDirectory', '../src/components/wiki/WikiSearch.jsx');
const { SourceChip } = await load('assistantThreadDirectory', '../src/components/assistant/assistantThread.jsx');

const OFFICE = {
  kind: 'office', id: 2, space_id: 11, name: 'Офис Алматы №1', city: 'Алматы',
  address: 'Улица Жамбыла, 172В', phone: '+7 700 123 45 67', no_office: false,
  schedule: null, day: null, closed_from: null, closed_until: null, updated_at: null,
};
const CITY = {
  kind: 'city', id: 1, space_id: 11, name: 'Алматы', narrowed: false,
  tariffs: [{ name: 'Эконом', commission: '17,1%' }, { name: 'Комфорт', commission: '19,2%' }],
  options: [{ name: 'По делам', commission: '9,3%' }], park_commission: null, range: '17,1–19,2%',
};
const ARTICLE = { id: 7, slug: 'a', title: 'Статья', snippet: '', highlights: [] };

/* ------------------------------------------------------------- подписи */

test('строка офиса: город, адрес и телефон одной строкой', () => {
  assert.deepEqual(directoryText(OFFICE), {
    title: 'Офис Алматы №1',
    subtitle: 'Алматы · Улица Жамбыла, 172В · +7 700 123 45 67',
  });
  const served = directoryText({ ...OFFICE, city: 'Караганда', for_city: 'Темиртау' });
  assert.match(served.subtitle, /^Караганда — для водителей из Темиртау · /);
  const absent = directoryText({ ...OFFICE, no_office: true, address: null, phone: null });
  assert.equal(absent.subtitle, 'Алматы · офиса в городе нет, принимают по телефону');
});

test('номер парка — с подписью, ключ строки не повторяется', () => {
  // Номер названного парка подписан: без подписи WhatsApp iTaxi читался бы номером офиса.
  const park = directoryText({ ...OFFICE, phone: '+7 700 765 43 21', phone_park: 'iTaxi',
                               phone_note: 'WhatsApp' });
  assert.equal(park.subtitle, 'Алматы · Улица Жамбыла, 172В · iTaxi: +7 700 765 43 21 (WhatsApp)');
  assert.notEqual(directoryKey(OFFICE), directoryKey({ ...OFFICE, for_city: 'Темиртау' }));
  assert.notEqual(directoryKey({ kind: 'offices_tab', city: null }),
                  directoryKey({ kind: 'cities_tab' }));
});

test('строка комиссий: тарифы, затем доп. опции; суженная — только тариф', () => {
  assert.deepEqual(directoryText(CITY), {
    title: 'Комиссия Яндекса · Алматы',
    subtitle: 'Эконом 17,1% · Комфорт 19,2% · доп. опции: 1',
  });
  const narrowed = directoryText({ ...CITY, narrowed: true, tariffs: [CITY.tariffs[0]] });
  assert.equal(narrowed.subtitle, 'Эконом 17,1%');
});

test('двери во вкладку: с городом и числом точек, без города и сводка по тарифу', () => {
  assert.equal(directoryText({ kind: 'offices_tab', city: 'Алматы', count: 8 }).subtitle,
    '8 точек — адреса, телефоны и статус на сегодня');
  assert.equal(directoryText({ kind: 'offices_tab', city: null }).title, 'Офисы');
  assert.equal(
    directoryText({ kind: 'cities_tab', tariffs: [{ name: 'Межгород', range: '11,4–17,1%', cities: 10 }] }).subtitle,
    'Межгород: 11,4–17,1% в 10 городах');
  assert.equal(directoryText({ kind: 'cities_tab', tariffs: [{ name: 'Эконом', range: '17,1%', cities: 1 }] }).subtitle,
    'Эконом: 17,1% в 1 городе');
});

/* ---------------------------------------------------------- двери и порядок */

test('строки без своей вкладки не рисуются', () => {
  const entries = [OFFICE, CITY, { kind: 'cities_tab', tariffs: [] }];
  assert.deepEqual(openableDirectory(entries, { offices: true, cities: false }), [OFFICE]);
  assert.deepEqual(openableDirectory(entries, { offices: false, cities: true }).map(directoryTab),
    ['cities', 'cities']);
  assert.deepEqual(openableDirectory(entries, {}), []);
});

test('справочник — первые строки, помощник — последняя', () => {
  const rows = searchRows([ARTICLE], true, [OFFICE, CITY]);
  assert.deepEqual(rows.map((row) => row.kind), ['directory', 'directory', 'article', 'assistant']);
  // Прежний вызов — прежний порядок.
  assert.deepEqual(searchRows([ARTICLE], true).map((row) => row.kind), ['article', 'assistant']);
});

/* ---------------------------------------------------------------- выдача */

const pane = (props) => renderToStaticMarkup(React.createElement(ResultsPane, {
  term: 'офис алматы', selectedIndex: 0, onHover() {}, onPick() {},
  brandModels: [], matchedBrand: null, activeCar: null, onPickCar() {},
  loading: false, failed: false, onRetry() {}, classifierFailed: false,
  listRef: null, maxHeight: '60vh', articleRows: [], fragmentRows: [], ...props,
}));

test('нашёлся только справочник — это ответ, а не «Ничего не найдено»', () => {
  const rows = searchRows([], false, [OFFICE]);
  const html = pane({ rows, directoryRows: rows });
  assert.match(html, /Справочник/);
  assert.match(html, /Офис Алматы №1/);
  assert.match(html, /data-row="0"/);
  assert.doesNotMatch(html, /Ничего не найдено/);
});

test('без справочника выдача прежняя', () => {
  const html = pane({ rows: [] });
  assert.match(html, /Ничего не найдено по запросу/);
  assert.doesNotMatch(html, /Справочник/);
});

/* ------------------------------------------------------ источник помощника */

const chip = (source) => renderToStaticMarkup(React.createElement(SourceChip, { source, onOpen() {} }));

test('чип справочника без статьи — рабочая кнопка во вкладку', () => {
  const html = chip({ title: 'Офисы', heading_path: 'Шымкент › Офис Шымкент', slug: '',
    tab: 'offices', ref_id: 8, quote: 'проспект Республики 17' });
  assert.doesNotMatch(html, /disabled/);
  assert.match(html, /Шымкент › Офис Шымкент/);
  const closed = chip({ title: 'Справочник недоступен', slug: null, tab: null, available: false });
  assert.match(closed, /disabled/);
  // Статья без slug по-прежнему не кнопка.
  assert.match(chip({ title: 'Статья', slug: '' }), /disabled/);
});
