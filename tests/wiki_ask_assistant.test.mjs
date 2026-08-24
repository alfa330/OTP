/*
 * «Спросить Помощника» в поиске вики.
 *
 * Что здесь стережётся и почему именно это.
 *
 * 1. ПОРЯДОК СТРОК ВЫДАЧИ. Помощник — не кнопка сбоку, а последняя строка
 *    списка, по которому ходят стрелки. Стоит ему уехать в середину или
 *    появиться при пустом запросе — и Enter в поиске начнёт открывать не то,
 *    что подсвечено. Проверять это кликами по выпадашке нечем, а сам список
 *    чистая функция.
 *
 * 2. КОГДА ПОМОЩНИК ГЛАВНЫЙ, А КОГДА ТИХИЙ. Нашлись статьи — строка под ними;
 *    не нашлось ничего — карточка вместо тупика «ничего не найдено». Ровно об
 *    это просил заказчик, и перепутать эти два состояния легче всего.
 *
 * 3. ЧТО ПОКАЗЫВАЕТСЯ БЕЗ ПОМОЩНИКА. Вкладку выключают тумблером пространства,
 *    и тогда выдача обязана выглядеть ровно как до этой правки — без строки,
 *    без карточки и со старым текстом пустого результата.
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

const search = await load('WikiSearch', '../src/components/wiki/WikiSearch.jsx');
const { ResultsPane, searchRows } = search;

const ARTICLE = {
  id: 7,
  slug: 'termopaket',
  title: 'Термопакеты',
  snippet: 'Выдаём <mark>термопакет</mark> в офисе',
  highlights: ['Выдаём <mark>термопакет</mark> в офисе', 'Второй <mark>термопакет</mark> платный'],
};

/* ---------------------------------------------------------------- порядок */

test('помощник — последняя строка выдачи, после статей и фрагментов', () => {
  const rows = searchRows([ARTICLE], true);
  assert.deepEqual(rows.map((r) => r.kind), ['article', 'fragment', 'assistant']);
});

test('без помощника список тот же, что был', () => {
  assert.deepEqual(searchRows([ARTICLE], false).map((r) => r.kind), ['article', 'fragment']);
});

test('пустая выдача с помощником — ровно одна строка, и она нулевая', () => {
  const rows = searchRows([], true);
  // Нулевая строка выделена по умолчанию: Enter сразу уносит вопрос в чат.
  assert.equal(rows.length, 1);
  assert.equal(rows[0].kind, 'assistant');
});

/* ----------------------------------------------------------------- рендер */

const pane = (props) => renderToStaticMarkup(React.createElement(ResultsPane, {
  term: 'термопакет',
  rows: [],
  articleRows: [],
  fragmentRows: [],
  selectedIndex: 0,
  onHover: () => {},
  onPick: () => {},
  brandModels: [],
  matchedBrand: null,
  activeCar: null,
  onPickCar: () => {},
  loading: false,
  failed: false,
  onRetry: () => {},
  classifierFailed: false,
  listRef: { current: null },
  maxHeight: '60vh',
  ...props,
}));

test('нашлись статьи — помощник тихой строкой под ними, без карточки', () => {
  const rows = searchRows([ARTICLE], true);
  const html = pane({
    rows,
    articleRows: rows.filter((r) => r.kind === 'article'),
    fragmentRows: rows.filter((r) => r.kind === 'fragment'),
  });
  assert.match(html, /Спросить Помощника/);
  assert.doesNotMatch(html, /Ничего не найдено/,
    'при найденных статьях пустого состояния быть не может');
  // Строка входит в клавиатурный список под своим номером.
  assert.match(html, /data-row="2"/);
});

test('не нашлось ничего — карточка помощника вместо тупика', () => {
  const html = pane({ rows: searchRows([], true) });
  assert.match(html, /Ничего не найдено по запросу «термопакет»/);
  assert.match(html, /Спросить Помощника/);
  // Карточка и строка одновременно — это дубль одного и того же действия.
  assert.equal(html.match(/Спросить Помощника/g).length, 1);
});

test('помощник выключен — старое пустое состояние и ни следа кнопки', () => {
  const html = pane({ rows: searchRows([], false) });
  assert.match(html, /Ничего не найдено по запросу «термопакет»/);
  assert.doesNotMatch(html, /Помощник/);
});

test('нашлась машина, а статей нет — главной остаётся карточка классификатора', () => {
  const html = pane({
    rows: searchRows([], true),
    activeCar: { brand: 'Toyota', model: 'Camry' },
  });
  assert.match(html, /ответ в карточке классификатора/);
  // Помощник при этом никуда не девается — он уходит вниз тихой строкой.
  assert.match(html, /Спросить Помощника/);
});

test('поиск не ответил — предлагаем и повтор, и помощника', () => {
  const html = pane({ rows: searchRows([], true), failed: true });
  assert.match(html, /Поиск не ответил/);
  assert.match(html, /Спросить Помощника/);
  assert.doesNotMatch(html, /Ничего не найдено/,
    'сетевой сбой — не пустая выдача, утверждать «не найдено» нельзя');
});
