/*
 * Календарь-панель раздела в режиме ОДНОЙ ДАТЫ.
 *
 * Режим `single` появился, когда системные `<input type="date">` в разделе
 * «Вики» меняли на панель раздела (эталон — выгрузка табло СЗоВ). Сетка месяца
 * в проекте одна и живёт в IosDateRangeCalendar, то есть тот же код обслуживает
 * и диапазон, и одиночную дату — сломать один режим правкой другого очень легко.
 *
 * Что именно охраняем:
 *  - ПУСТОЕ значение не подсвечивает никакой день. Первая версия примитива
 *    передавала в календарь месяц-якорь через `from`, и незаполненное поле
 *    встречало человека уже «выбранным» сегодняшним днём.
 *  - Границы min/max реально гасят дни, а не только надеются на браузер:
 *    раньше их обеспечивало системное поле, теперь наш код.
 *  - В одиночном режиме нет пресета «Весь период» и подсказки про начало и
 *    конец периода — они относятся к диапазону и здесь были бы шумом.
 *  - Диапазонный режим не пострадал: у него всё это на месте.
 *
 * Браузерного окружения в проекте нет (ни jsdom, ни puppeteer), поэтому
 * настоящий компонент рендерится через react-dom/server. JSX не используется
 * намеренно: node --test гоняет .mjs без сборки.
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

const SOURCE = new URL('../src/components/ui/DateRangePicker.jsx', import.meta.url);

async function loadCalendar() {
  const jsx = readFileSync(SOURCE, 'utf8');
  const { code } = transformSync(jsx, { loader: 'jsx', format: 'esm', target: 'node18' });
  /* Иконки lucide-react тянут за собой весь пакет — подменяем заглушками:
     проверяем разметку календаря, а не то, как нарисована стрелка. */
  const patched = code.replace(
    /import\s*\{[^}]*\}\s*from\s*['"]lucide-react['"];?/,
    'const Stub = () => null; const Calendar = Stub, ChevronUp = Stub, ChevronLeft = Stub, ChevronRight = Stub;',
  );
  /* Собранный модуль кладём ВНУТРЬ проекта: из системной временной папки
     `import 'react'` не разрешается — node ищет node_modules вверх от файла. */
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const file = join(dir, 'DateRangePicker.mjs');
  writeFileSync(file, patched, 'utf8');
  const mod = await import(`file://${file.replace(/\\/g, '/')}`);
  return mod.IosDateRangeCalendar;
}

const IosDateRangeCalendar = await loadCalendar();

const render = (props) => renderToStaticMarkup(
  React.createElement(IosDateRangeCalendar, { onChange: () => {}, ...props }),
);

// Выбранный день рисуется синей таблеткой — по ней и считаем.
const SELECTED = 'bg-blue-500';
const countSelected = (html) => html.split(SELECTED).length - 1;

test('одиночный режим: пустое значение не подсвечивает ни одного дня', () => {
  const html = render({ single: true, from: '', to: '', initialMonth: '2026-08-24' });
  assert.equal(countSelected(html), 0, 'незаполненное поле не должно выглядеть заполненным');
});

test('одиночный режим: выбранный день подсвечен ровно один', () => {
  const html = render({ single: true, from: '2026-08-24', to: '2026-08-24' });
  assert.equal(countSelected(html), 1);
});

test('месяц берётся из initialMonth, когда даты ещё нет', () => {
  const html = render({ single: true, from: '', to: '', initialMonth: '2026-02-10' });
  assert.ok(html.includes('Февраль 2026'), 'календарь должен открыться на месяце-якоре');
  assert.ok(!html.includes('Август'), 'сегодняшний месяц не должен перебивать якорь');
});

test('max гасит дни после границы, min — до неё', () => {
  const late = render({ single: true, from: '', to: '', initialMonth: '2026-08-01', max: '2026-08-10' });
  const early = render({ single: true, from: '', to: '', initialMonth: '2026-08-01', min: '2026-08-20' });
  // Недоступный день помечается курсором-запретом — он и есть признак границы.
  const blocked = (html) => html.split('cursor-not-allowed').length - 1;
  assert.ok(blocked(late) >= 21, `после 10 августа должен гаснуть весь хвост месяца, погашено ${blocked(late)}`);
  assert.ok(blocked(early) >= 19, `до 20 августа должно гаснуть начало месяца, погашено ${blocked(early)}`);
});

test('одиночный режим не предлагает «Весь период» и не спрашивает про конец периода', () => {
  const html = render({ single: true, from: '2026-08-24', to: '2026-08-24' });
  assert.ok(!html.includes('Весь период'), 'пресет диапазона в одиночном режиме бессмыслен');
  assert.ok(!html.includes('начало периода'), 'подсказка про два клика относится к диапазону');
  assert.ok(html.includes('Сегодня'), 'быстрый переход на сегодня остаётся');
});

test('диапазонный режим не задет: пресеты и подсказка на месте', () => {
  const html = render({ from: '2026-08-01', to: '2026-08-10' });
  assert.ok(html.includes('Весь период'));
  assert.ok(html.includes('начало периода'));
});

test('диапазон по-прежнему подсвечивает полосу между краями', () => {
  const html = render({ from: '2026-08-01', to: '2026-08-10' });
  assert.ok(html.includes('bg-blue-500/10'), 'полоса выбранного периода должна рисоваться');
});
