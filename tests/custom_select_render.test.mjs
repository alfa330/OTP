/*
 * CustomSelect — общий примитив, он стоит в десятках разделов, и его легко
 * сломать «мимоходом», добавляя новый режим. Здесь настоящий компонент
 * отрисовывается через react-dom/server: браузерного окружения в проекте нет
 * (ни jsdom, ни puppeteer), а серверный рендер закрывает главное — что стоит в
 * кнопке при разных `value`.
 *
 * Что именно защищаем: ПУСТАЯ СТРОКА — законное значение опции. В проекте это
 * «Все отделы», «Все группы», «— не задан —», «Текущий состав». Регресс,
 * из-за которого кнопка вместо такой подписи показывала placeholder, ловится
 * ровно этим тестом.
 *
 * JSX здесь не используется намеренно: node --test гоняет .mjs без сборки, а
 * React.createElement читается ничуть не хуже.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

// Компонент — .jsx, его надо собрать перед импортом: esbuild уже в зависимостях
// (через vite), отдельный инструмент не нужен.
const { transformSync } = require('esbuild');
const { readFileSync, writeFileSync, mkdirSync } = require('node:fs');
const { join } = require('node:path');

const SOURCE = new URL('../src/components/ui/CustomSelect.jsx', import.meta.url);

async function loadCustomSelect() {
  const jsx = readFileSync(SOURCE, 'utf8');
  const { code } = transformSync(jsx, { loader: 'jsx', format: 'esm', target: 'node18' });
  // Импорт './ios' тянет только константу шрифта — подменяем, чтобы не тащить
  // за собой цепочку зависимостей UI-кита.
  // Кавычки после esbuild двойные, поэтому в шаблоне допускаем любые.
  const patched = code.replace(
    /import\s*\{[^}]*\}\s*from\s*['"]\.\/ios['"];?/,
    "const APPLE_FONT = 'system-ui';",
  );
  /* Собранный модуль кладём ВНУТРЬ проекта: из системной временной папки
     `import 'react'` не разрешается — node ищет node_modules вверх от файла.
     node_modules/.cache для этого и существует и в git не попадает. */
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const file = join(dir, 'CustomSelect.mjs');
  writeFileSync(file, patched, 'utf8');
  const mod = await import(`file://${file.replace(/\\/g, '/')}`);
  return mod.default;
}

const CustomSelect = await loadCustomSelect();

const DEPARTMENTS = [
  { value: '', label: 'Все отделы' },
  { value: '1', label: 'СЗоВ' },
  { value: '2', label: 'Отдел продаж' },
];

const render = (props) =>
  renderToStaticMarkup(React.createElement(CustomSelect, { options: DEPARTMENTS, ...props }));

test('пустая строка — законное значение: в кнопке стоит подпись опции, а не placeholder', () => {
  const html = render({ value: '', placeholder: 'Выберите...' });
  assert.ok(html.includes('Все отделы'), 'подпись опции со значением "" должна стоять в кнопке');
  assert.ok(!html.includes('Выберите...'), 'placeholder не должен подменять выбранную опцию');
});

test('пустое значение и «значения нет» — это одно состояние', () => {
  /* Ради этого опция со значением '' и заводится: она И ЕСТЬ подпись состояния
     «ничего не выбрано» («Все отделы» = фильтр не задан). Поэтому undefined и
     null показываются ею же, а не placeholder'ом — так было и до мультирежима. */
  for (const value of [undefined, null, '']) {
    const html = render({ value, placeholder: 'Выберите...' });
    assert.ok(html.includes('Все отделы'), `при value=${value} ожидалась подпись пустой опции`);
    assert.ok(!html.includes('Выберите...'), `при value=${value} placeholder лишний`);
  }
});

test('placeholder показывается, когда ни одна опция не подходит', () => {
  // Здесь пустой опции в списке нет — значит выбор действительно не сделан.
  const options = [{ value: '1', label: 'СЗоВ' }];
  const html = renderToStaticMarkup(React.createElement(CustomSelect, {
    options, value: '', placeholder: 'Выберите...',
  }));
  assert.ok(html.includes('Выберите...'));
  assert.ok(!html.includes('СЗоВ'));
});

test('обычное значение показывается своей подписью', () => {
  const html = render({ value: '2', placeholder: 'Выберите...' });
  assert.ok(html.includes('Отдел продаж'));
  assert.ok(!html.includes('Выберите...'));
});

test('число и строка — одно и то же значение', () => {
  // Половина вызывающих держит id числом, половина строкой.
  assert.ok(render({ value: 1 }).includes('СЗоВ'));
  assert.ok(render({ value: '1' }).includes('СЗоВ'));
});

test('мультирежим: пустой состав даёт placeholder, непустой — «Выбрано: N»', () => {
  const empty = render({ multiple: true, value: [], placeholder: 'Выберите сотрудника' });
  assert.ok(empty.includes('Выберите сотрудника'));

  const two = render({ multiple: true, value: ['1', '2'] });
  assert.ok(two.includes('Выбрано: 2'), 'без renderValue в кнопке стоит счётчик');
});

test('мультирежим: renderValue рисует своё', () => {
  const html = render({
    multiple: true,
    value: ['1', '2'],
    renderValue: (ids) => `выбрано ${ids.length} человек`,
  });
  assert.ok(html.includes('выбрано 2 человек'));
  assert.ok(!html.includes('Выбрано: 2'));
});

test('мультирежим объявлен множественным для скринридеров, одиночный — нет', () => {
  // aria-multiselectable рисуется на списке, а он появляется только открытым;
  // проверяем сам факт, что атрибут не протёк в одиночный режим кнопки.
  assert.ok(!render({ value: '1' }).includes('aria-multiselectable'));
});

test('мусор в значении не роняет рендер', () => {
  for (const value of [[], [null], ['', null], 0, false]) {
    assert.doesNotThrow(() => render({ multiple: Array.isArray(value), value }));
  }
});
