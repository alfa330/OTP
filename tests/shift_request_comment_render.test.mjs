/*
 * Причина оператора в заявке по смене — блок-цитата (задача #349).
 *
 * До задачи причина была самой бледной строкой карточки, и руководитель её
 * пропускал. Что именно охраняем:
 *  - пустая причина не рисует ничего: цитата без текста — рамка ни о чём, а
 *    причины нет у каждой второй заявки;
 *  - текст выводится целиком, с переносами строк оператора, и не обрезается;
 *  - разметка не опирается на flex и произвольную сетку: оболочка телефона
 *    (mobile-shell.css) даёт скруглённому серому flex-блоку боковую прокрутку,
 *    а сетку grid-cols-[…] схлопывает в одну колонку.
 *
 * Настоящий компонент рендерится через react-dom/server, как и остальные
 * примитивы: браузерного окружения в тестах нет.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { transformSync } = require('esbuild');
const { readFileSync, writeFileSync, mkdirSync } = require('node:fs');
const { join } = require('node:path');

const SOURCE = new URL('../src/components/schedule/ShiftRequestComment.jsx', import.meta.url);

async function loadComment() {
  const jsx = readFileSync(SOURCE, 'utf8');
  const { code } = transformSync(jsx, { loader: 'jsx', format: 'esm', target: 'node18' });
  // Значок кавычек — заглушкой с меткой: проверяем разметку блока, а не рисунок.
  const patched = code.replace(
    /import\s*\{[^}]*\}\s*from\s*['"]lucide-react['"];?/,
    "const Quote = () => React.createElement('i', { 'data-icon': 'quote' });",
  );
  assert.notEqual(patched, code, 'импорт значков не найден — заглушка не подставилась');
  // Собранный модуль кладём внутрь проекта: из системной временной папки
  // `import 'react'` не разрешается — node ищет node_modules вверх от файла.
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const file = join(dir, 'ShiftRequestComment.mjs');
  writeFileSync(file, patched, 'utf8');
  const mod = await import(`file://${file.replace(/\\/g, '/')}`);
  return mod.default;
}

const ShiftRequestComment = await loadComment();
const render = (props) => renderToStaticMarkup(React.createElement(ShiftRequestComment, props));

test('без причины блок не рисуется вовсе', () => {
  for (const text of [undefined, null, '', '   ', '\n\t ']) {
    assert.equal(render({ text }), '', `пустая причина ${JSON.stringify(text)} не должна оставлять рамку`);
  }
});

test('причина выводится целиком, с кавычками и переносами строк', () => {
  const text = 'Первая строка причины.\nВторая строка причины';
  const html = render({ text });
  assert.ok(html.includes('data-icon="quote"'), 'значок цитаты на месте');
  assert.ok(html.includes('Первая строка причины.\nВторая строка причины'));
  assert.ok(html.includes('whitespace-pre-line'), 'перенос строки оператора должен сохраняться');
  assert.ok(html.includes('break-words'), 'длинное слово без пробелов рвётся, а не раздвигает карточку');
});

test('края обрезаются, внутренний текст — нет', () => {
  const long = `  ${'очень длинная причина '.repeat(20)} `;
  const html = render({ text: long });
  assert.ok(html.includes(long.trim()));
  for (const cut of ['truncate', 'line-clamp', 'overflow-hidden', 'text-ellipsis']) {
    assert.ok(!html.includes(cut), `класс ${cut} обрезал бы причину`);
  }
});

test('текст оператора не становится разметкой', () => {
  const html = render({ text: '<b>срочно</b> & важно' });
  assert.ok(html.includes('&lt;b&gt;срочно&lt;/b&gt; &amp; важно'));
  assert.ok(!html.includes('<b>'));
});

test('блок не flex и не произвольная сетка, цвет нейтральный', () => {
  const html = render({ text: 'пары с 13:00', className: 'mt-1.5' });
  const classes = /^<p class="([^"]*)"/.exec(html)?.[1].split(/\s+/) || [];
  assert.ok(classes.length > 0, 'корень блока — абзац с классами');
  for (const trap of ['flex', 'inline-flex', 'grid']) {
    assert.ok(!classes.includes(trap), `${trap}: оболочка телефона перекроит такой блок`);
  }
  assert.ok(!classes.some((name) => name.startsWith('grid-cols-[')));
  assert.ok(classes.includes('bg-slate-50'), 'фон нейтральный');
  assert.ok(!classes.some((name) => /^(bg|text|ring)-(amber|rose|red|blue|emerald|green|indigo)-/.test(name)),
    'цвет несёт смысл, а причина есть у половины заявок — красить её нельзя');
  assert.ok(classes.includes('mt-1.5'), 'внешний отступ задаёт место вставки');
});
