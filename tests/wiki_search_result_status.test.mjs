/*
 * Актуальность статьи в выдаче поиска (задача #315, Кастек Гаухар).
 *
 * Что стережётся и почему именно это.
 *
 * 1. ШТАМП ЕСТЬ У КАЖДОЙ СТРОКИ, включая опубликованную. На боевой базе 242
 *    черновика из 352, и правило «нет бейджа значит опубликована» было бы
 *    кодом, который надо выучить, — ровно в том месте, где заказчик просил
 *    ответ глазами. Потерять это легко: во всех остальных местах раздела
 *    (подсказка внутренних ссылок) помечены только черновик и архив.
 *
 * 2. ФРАГМЕНТЫ ШТАМПА НЕ ПОЛУЧАЮТ. Секция «Совпадения в тексте» — это куски
 *    тех же статей, что уже перечислены выше; второй бейдж на ту же статью
 *    читается как вторая статья.
 *
 * 3. ДАТА ПОДПИСАНА СЛОВОМ «добавлена». Рядом стоит статус, и голое число
 *    читалось бы как дата обновления, то есть отвечало бы на другой вопрос.
 *
 * 4. НЕТ ДАТЫ — НЕТ СТРОКИ. Сервер отдаёт created_at всегда, но выдача
 *    попадает во фронт и из старого кеша ответа, и падать на этом нельзя.
 *
 * Часовой пояс закреплён: даты сравниваются с календарным днём, а прогон в
 * CI идёт в UTC, у меня — в Алматы.
 */
process.env.TZ = 'Asia/Almaty';

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
const { ResultsPane, searchRows, addedOn } = search;

const article = (id, status, createdAt, extra = {}) => ({
  id,
  slug: `st-${id}`,
  title: `Аренда транспорта ${id}`,
  status,
  created_at: createdAt,
  summary: 'Условия аренды',
  ...extra,
});

const pane = (items) => {
  const rows = searchRows(items, false);
  return renderToStaticMarkup(React.createElement(ResultsPane, {
    term: 'аренда',
    rows,
    articleRows: rows.filter((r) => r.kind === 'article'),
    fragmentRows: rows.filter((r) => r.kind === 'fragment'),
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
  }));
};

/* ------------------------------------------------------------------ дата */

test('дата добавления подписана словом и сокращена до дд.мм.гг', () => {
  assert.equal(addedOn('2026-09-11T12:00:00'), 'добавлена 11.09.26');
});

test('дата с сервера (RFC 1123, GMT) читается тем же днём', () => {
  // Именно в таком виде её отдаёт Flask: jsonify сериализует TIMESTAMP так.
  assert.equal(addedOn('Fri, 11 Sep 2026 17:44:06 GMT'), 'добавлена 11.09.26');
});

test('пустая и битая дата не дают строки', () => {
  assert.equal(addedOn(null), '');
  assert.equal(addedOn(''), '');
  assert.equal(addedOn('позавчера'), '');
});

/* ----------------------------------------------------------------- выдача */

test('все три состояния подписаны в строке выдачи', () => {
  const html = pane([
    article(1, 'published', '2026-09-11T12:00:00'),
    article(2, 'draft', '2026-08-09T12:00:00'),
    article(3, 'archived', '2026-08-20T12:00:00'),
  ]);
  assert.match(html, /Опубликована/);
  assert.match(html, /Черновик/);
  assert.match(html, /В архиве/);
  assert.match(html, /добавлена 11\.09\.26/);
  assert.match(html, /добавлена 09\.08\.26/);
  assert.match(html, /добавлена 20\.08\.26/);
});

test('опубликованная статья тоже со штампом, а не «без бейджа значит живая»', () => {
  const html = pane([article(1, 'published', '2026-09-11T12:00:00')]);
  assert.match(html, /Опубликована/);
});

test('фрагменты той же статьи второго бейджа не получают', () => {
  const html = pane([article(2, 'draft', '2026-08-09T12:00:00', {
    snippet: 'Условия <mark>аренды</mark>',
    highlights: ['Условия <mark>аренды</mark>', 'Вторая <mark>аренда</mark>'],
  })]);
  // Фрагмент в выдаче есть, а бейдж на статью — ровно один.
  assert.match(html, /Совпадения в тексте/);
  assert.equal(html.match(/Черновик/g).length, 1);
  assert.equal(html.match(/добавлена 09\.08\.26/g).length, 1);
});

test('статья без даты остаётся строкой выдачи, просто без даты', () => {
  const html = pane([article(1, 'draft', null)]);
  assert.match(html, /Аренда транспорта 1/);
  assert.match(html, /Черновик/);
  assert.doesNotMatch(html, /добавлена/);
});

test('незнакомый статус показываем как есть, а не прячем строку', () => {
  const html = pane([article(1, 'on_approval', '2026-09-11T12:00:00')]);
  assert.match(html, /На согласовании/);
});
