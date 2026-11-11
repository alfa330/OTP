/*
 * Календарь ПЕРИОДА (задача #389): год, месяц, конкретный день или диапазон
 * дат и быстрые периоды «Сегодня», «Неделя», «Месяц», «Весь период» — в
 * ИИ-оценке («Обзор», «Сводка», «База разборов») и в «Аналитике» журнала оценок.
 *
 * Правила живут в src/components/ui/periodRange.js и общие для двух календарей:
 * портального (IosDateRangeCalendar с `views`) и журнального (своя сборка без
 * Tailwind, EvalPeriodPicker). Разойдись они — один и тот же «Месяц» значил бы
 * в двух разделах разное, поэтому правила под тестом, а не в комментариях.
 *
 * Вторая половина — разметка портального календаря через react-dom/server: вид
 * «Месяцы» и «Годы», шесть недель всегда (карточка не прыгает), четыре быстрых
 * периода сеткой 2 × 2 и подсветка совпавшего, а без `views` — прежний календарь.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

import {
    YEAR_PAGE, dayCells, initialPeriodView, monthCells, monthRange, parseIso, periodHeader, periodLabel,
    portalTabStep, presetMatches, quickPeriods, shiftIso, visibleYearCells, wholeMonthOf, wholeMonthsOf,
    wholeYearOf, yearCells, yearPageEnd, yearRange,
} from '../src/components/ui/periodRange.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { transformSync } = require('esbuild');
const { readFileSync, writeFileSync, mkdirSync } = require('node:fs');
const { join } = require('node:path');

/* ── правила ───────────────────────────────────────────────────────────── */

test('parseIso принимает только настоящую дату', () => {
    assert.equal(parseIso('2026-02-29'), null, '29 февраля 2026 нет');
    assert.equal(parseIso('2026-13-01'), null);
    assert.equal(parseIso(''), null);
    assert.ok(parseIso('2024-02-29'));
    assert.equal(shiftIso('2026-03-01', -1), '2026-02-28');
    assert.equal(shiftIso('2026-12-31', 1), '2027-01-01');
});

test('границы месяца и года', () => {
    assert.deepEqual(monthRange(2026, 1), { from: '2026-02-01', to: '2026-02-28' });
    assert.deepEqual(monthRange(2024, 1), { from: '2024-02-01', to: '2024-02-29' });
    assert.deepEqual(yearRange(2025), { from: '2025-01-01', to: '2025-12-31' });
});

test('целый месяц, целый год и период из целых месяцев', () => {
    assert.equal(wholeMonthOf('2026-10-01', '2026-10-31'), '2026-10');
    assert.equal(wholeMonthOf('2026-10-01', '2026-10-30'), null, 'без последнего дня — не месяц');
    assert.equal(wholeMonthOf('2026-10-02', '2026-10-31'), null);
    assert.equal(wholeMonthOf('2026-09-01', '2026-10-31'), null, 'два месяца — не «месяц»');
    assert.equal(wholeYearOf('2026-01-01', '2026-12-31'), '2026');
    assert.equal(wholeYearOf('2026-01-01', '2026-12-30'), null);
    assert.deepEqual(wholeMonthsOf('2026-09-01', '2026-10-31'), ['2026-09', '2026-10']);
    assert.deepEqual(wholeMonthsOf('2025-11-01', '2026-02-28'), ['2025-11', '2025-12', '2026-01', '2026-02']);
    assert.equal(wholeMonthsOf('2026-09-01', '2026-10-15'), null, 'хвост месяца — уже не целые месяцы');
    assert.equal(wholeMonthsOf('2026-09-02', '2026-10-31'), null);
    assert.equal(wholeMonthsOf('', ''), null, '«Весь период» не превращается в список месяцев');
    assert.equal(wholeMonthsOf('2026-10-01', '2026-09-30'), null, 'перевёрнутый период');
    assert.equal(wholeMonthsOf('2026-01-01', '2026-12-31').length, 12);
});

test('подпись: весь период, год, месяц, день, диапазон', () => {
    assert.equal(periodLabel('', ''), 'Весь период');
    assert.equal(periodLabel('2026-01-01', '2026-12-31'), '2026 год');
    assert.equal(periodLabel('2026-10-01', '2026-10-31'), 'Октябрь 2026');
    assert.equal(periodLabel('2026-11-10', '2026-11-10'), '10 ноя 2026');
    assert.equal(periodLabel('2026-11-04', '2026-11-10'), '4 ноя — 10 ноя 2026');
    assert.equal(periodLabel('2025-12-20', '2026-01-05'), '20 дек 2025 — 5 янв 2026');
    assert.equal(periodLabel('2026-11-04', ''), 'с 4 ноя 2026');
});

test('быстрые периоды: «Неделя» и «Месяц» — 7 и 30 дней по сегодня, «Весь период» — пусто', () => {
    const presets = quickPeriods(new Date(2026, 10, 10, 9, 0));
    assert.deepEqual(presets.map((p) => p.label), ['Сегодня', 'Неделя', 'Месяц', 'Весь период']);
    const byKey = Object.fromEntries(presets.map((p) => [p.key, p.range()]));
    assert.deepEqual(byKey.today, { from: '2026-11-10', to: '2026-11-10' });
    assert.deepEqual(byKey.week, { from: '2026-11-04', to: '2026-11-10' });
    assert.deepEqual(byKey.month, { from: '2026-10-12', to: '2026-11-10' });
    assert.deepEqual(byKey.all, { from: '', to: '' });
    assert.ok(presetMatches(presets[3], '', ''), '«Весь период» совпадает с пустым выбором');
    assert.ok(presetMatches(presets[1], '2026-11-04', '2026-11-10'));
    assert.ok(!presetMatches(presets[1], '2026-11-03', '2026-11-10'));
});

test('вид при открытии: год — «Годы», месяц — «Месяцы», остальное — дни', () => {
    assert.equal(initialPeriodView('2026-01-01', '2026-12-31'), 'years');
    assert.equal(initialPeriodView('2026-10-01', '2026-10-31'), 'months');
    assert.equal(initialPeriodView('2026-10-01', '2026-10-15'), 'days');
    assert.equal(initialPeriodView('', ''), 'days');
    assert.equal(initialPeriodView('2026-01-01', '2026-12-31', ['days', 'months']), 'days',
        'вида «Годы» нет — открываемся днями');
});

test('месяцы: текущий доступен при max = сегодня, будущие — нет, выбранный отмечен', () => {
    const cells = monthCells(2026, { from: '2026-10-01', to: '2026-10-31', max: '2026-11-10', today: '2026-11-10' });
    assert.equal(cells.length, 12);
    const by = Object.fromEntries(cells.map((c) => [c.key, c]));
    assert.equal(by['2026-11'].disabled, false, 'ноябрь уже идёт — его можно выбрать');
    assert.equal(by['2026-11'].current, true);
    assert.equal(by['2026-12'].disabled, true, 'декабрь целиком в будущем');
    assert.equal(by['2026-10'].selected, true);
    assert.equal(cells.filter((c) => c.selected).length, 1);
    assert.deepEqual(by['2026-02'].range, { from: '2026-02-01', to: '2026-02-28' });
    const early = monthCells(2026, { min: '2026-07-15' });
    assert.equal(early.find((c) => c.key === '2026-06').disabled, true);
    assert.equal(early.find((c) => c.key === '2026-07').disabled, false, 'июль частично в границах');
});

test('годы: страница из двенадцати лет кончается годом границы', () => {
    assert.equal(YEAR_PAGE, 12);
    assert.equal(yearPageEnd(2026, 2026), 2026);
    assert.equal(yearPageEnd(2015, 2026), 2026);
    assert.equal(yearPageEnd(2014, 2026), 2014);
    assert.equal(yearPageEnd(2027, 2026), 2038);
    const cells = yearCells(2026, { from: '2026-01-01', to: '2026-12-31', max: '2026-11-10', today: '2026-11-10' });
    assert.deepEqual([cells[0].key, cells[11].key], ['2015', '2026']);
    assert.equal(cells.find((c) => c.key === '2026').selected, true);
    assert.equal(cells.find((c) => c.key === '2026').disabled, false);
    const next = yearCells(2038, { max: '2026-11-10' });
    assert.ok(next.every((c) => c.disabled), 'годы после границы недоступны');
});

test('годы до нижней границы скрыты, шапка «Годов» — первый и последний видимые', () => {
    const years = visibleYearCells(2026, { min: '2025-01-01', max: '2026-11-10' });
    assert.deepEqual(years.map((c) => c.key), ['2025', '2026']);
    assert.equal(visibleYearCells(2026, { max: '2026-11-10' }).length, YEAR_PAGE, 'без границы — вся страница');
    const head = periodHeader('years', { yearsEnd: 2026, years, min: '2025-01-01', max: '2026-11-10' });
    assert.equal(head.title, '2025 – 2026');
    assert.equal(head.prevDisabled, true, 'раньше 2015-го журнала нет — листать некуда');
    assert.equal(head.nextDisabled, true, '2027 позже границы');
    assert.equal(periodHeader('years', { yearsEnd: 2026, years: years.slice(1), min: '2026-01-01' }).title, '2026');
});

test('шапка «Месяцев» и «Дней»: год и месяц словами, листание — в пределах границ', () => {
    const months = periodHeader('months', { view: { y: 2025, m: 0 }, min: '2025-01-01', max: '2026-11-10' });
    assert.deepEqual([months.title, months.prevDisabled, months.nextDisabled], ['2025', true, false]);
    assert.equal(periodHeader('months', { view: { y: 2026, m: 0 }, max: '2026-11-10' }).nextDisabled, true);
    assert.equal(periodHeader('months', { view: { y: 2026, m: 0 }, min: '2025-01-01' }).prevDisabled, false);
    const days = periodHeader('days', { view: { y: 2026, m: 10 }, min: '2026-01-01', max: '2026-11-10' });
    assert.deepEqual([days.title, days.prevDisabled, days.nextDisabled], ['Ноябрь 2026', false, false],
        'в днях листание не ограничено, как и было: недоступные дни просто серые');
    assert.deepEqual([days.prevLabel, months.prevLabel], ['Предыдущий месяц', 'Предыдущий год']);
});

test('Tab у календаря в портале: с кнопки — в панель, с первой назад — на кнопку, с последней — дальше', () => {
    // Разбор #389: в журнале Tab с открытой кнопки уходил мимо панели, а та висела открытой.
    assert.equal(portalTabStep({ onTrigger: true, count: 20 }), 'first');
    assert.equal(portalTabStep({ onTrigger: true, count: 20, shift: true }), null, 'Shift+Tab с кнопки — обычный');
    assert.equal(portalTabStep({ index: 0, count: 20, shift: true }), 'trigger');
    assert.equal(portalTabStep({ index: 19, count: 20 }), 'leave');
    assert.equal(portalTabStep({ index: 5, count: 20 }), null, 'внутри панели — обычный Tab');
    assert.equal(portalTabStep({ onTrigger: true, count: 0 }), null, 'пустая панель — ничего не перехватываем');
});

/* ── разметка календаря ────────────────────────────────────────────────── */

async function loadCalendar() {
    const source = new URL('../src/components/ui/DateRangePicker.jsx', import.meta.url);
    const { code } = transformSync(readFileSync(source, 'utf8'), { loader: 'jsx', format: 'esm', target: 'node18' });
    const patched = code.replace(
        /import\s*\{[^}]*\}\s*from\s*['"]lucide-react['"];?/,
        'const Stub = () => null; const Calendar = Stub, Check = Stub, ChevronUp = Stub, ChevronLeft = Stub, ChevronRight = Stub;',
    ).replace(
        /from\s*['"]\.\/periodRange\.js['"]/,
        `from '${new URL('../src/components/ui/periodRange.js', import.meta.url).href}'`,
    );
    const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
    mkdirSync(dir, { recursive: true });
    const file = join(dir, 'DateRangePickerPeriod.mjs');
    writeFileSync(file, patched, 'utf8');
    return import(`file://${file.replace(/\\/g, '/')}`);
}

const picker = await loadCalendar();
const VIEWS = ['days', 'months', 'years'];
const render = (props) => renderToStaticMarkup(React.createElement(picker.IosDateRangeCalendar, {
    onChange: () => {}, presets: quickPeriods(), views: VIEWS, max: '2026-11-10', ...props,
}));
const tabs = (html) => [...html.matchAll(/role="tab" aria-selected="(true|false)"[^>]*>([^<]+)</g)]
    .map((m) => `${m[2]}${m[1] === 'true' ? '*' : ''}`);

test('переключатель видов «Дни · Месяцы · Годы»; целый месяц открывается «Месяцами»', () => {
    assert.deepEqual(tabs(render({ from: '', to: '' })), ['Дни*', 'Месяцы', 'Годы']);
    const month = render({ from: '2026-10-01', to: '2026-10-31' });
    assert.deepEqual(tabs(month), ['Дни', 'Месяцы*', 'Годы']);
    assert.ok(month.includes('>Окт<'), 'в сетке месяцев есть октябрь');
    assert.ok(month.includes('Выберите месяц'));
    assert.match(month, /aria-pressed="true"[^>]*>Окт</, 'выбранный месяц отмечен');
    const year = render({ from: '2026-01-01', to: '2026-12-31' });
    assert.deepEqual(tabs(year), ['Дни', 'Месяцы', 'Годы*']);
    assert.ok(year.includes('2015 – 2026'));
    assert.ok(year.includes('Выберите год'));
});

test('шесть недель всегда: в месяце из пяти недель сетка дорисована пустыми клетками', () => {
    // Октябрь 2026 начинается в четверг и занимает пять недель (3 + 31 = 34 клетки).
    const html = render({ from: '2026-10-05', to: '2026-10-09' });
    const grid = html.split('grid grid-cols-7 gap-y-0.5')[1];
    const cellCount = (grid.match(/class="(relative flex )?h-9 w-9/g) || []).length;
    assert.equal(cellCount, 42);
});

test('четыре быстрых периода — сеткой 2 × 2, совпавший подсвечен', () => {
    const html = render({ from: '', to: '' });
    for (const label of ['Сегодня', 'Неделя', 'Месяц', 'Весь период']) assert.ok(html.includes(label), label);
    assert.ok(html.includes('grid grid-cols-2 gap-2'));
    assert.match(html, /aria-pressed="true"[^>]*>Весь период</, '«Весь период» подсвечен при пустом выборе');
    assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
});

test('без views — прежний календарь: ни переключателя, ни сетки 2 × 2, ни подсветки', () => {
    const html = renderToStaticMarkup(React.createElement(picker.IosDateRangeCalendar, {
        onChange: () => {}, from: '', to: '', presets: quickPeriods(),
    }));
    assert.ok(!html.includes('role="tablist"'));
    assert.ok(!html.includes('grid grid-cols-2 gap-2'));
    assert.ok(!html.includes('aria-pressed'));
    assert.ok(html.includes('Выберите начало периода'));
});

test('чип периода подписывает месяц и год словами, прежний чип — двумя датами', () => {
    const chip = (props) => renderToStaticMarkup(React.createElement(picker.IosDateRangePicker, {
        onChange: () => {}, ...props,
    }));
    assert.ok(chip({ from: '2026-10-01', to: '2026-10-31', views: VIEWS }).includes('Октябрь 2026'));
    assert.ok(chip({ from: '2026-01-01', to: '2026-12-31', views: VIEWS }).includes('2026 год'));
    assert.ok(chip({ from: '2026-10-01', to: '2026-10-31' }).includes('1 окт — 31 окт 2026'));
    assert.ok(chip({ from: '', to: '', views: VIEWS }).includes('aria-expanded="false"'));
});

test('годы до нижней границы не показываются, раньше неё не листается', () => {
    const html = render({ from: '2025-01-01', to: '2025-12-31', min: '2025-01-01' });
    assert.deepEqual(tabs(html), ['Дни', 'Месяцы', 'Годы*']);
    const years = [...html.matchAll(/title="(\d{4}) год"/g)].map((m) => m[1]);
    assert.deepEqual(years, ['2025', '2026'], 'журнал ведётся с 2025 — 2015…2024 не предлагаются');
    assert.ok(html.includes('2025 – 2026'));
    assert.match(html, /aria-label="Более ранние годы" disabled=""/);
    const months = render({ from: '2025-10-01', to: '2025-10-31', min: '2025-01-01' });
    assert.match(months, /aria-label="Предыдущий год" disabled=""/, '2024 целиком раньше границы');
    const single = render({ from: '', to: '', min: '2026-01-01' });
    assert.ok(!single.includes('Предыдущий год" disabled'), 'в днях листание назад не ограничено, как и было');
});

test('подсветка быстрого периода — только при совпадении обоих краёв; края полосы — не «внутри»', () => {
    const [, week] = quickPeriods(new Date(2026, 10, 10, 9, 0));
    assert.ok(presetMatches(week, '2026-11-04', '2026-11-10'));
    assert.ok(!presetMatches(week, '2026-11-04', '2026-11-09'), 'начало совпало, конец нет — это не «Неделя»');
    const cells = Object.fromEntries(dayCells(2026, 10, { lo: '2026-11-04', hi: '2026-11-10' })
        .filter((c) => c.iso).map((c) => [c.iso, c]));
    assert.equal(cells['2026-11-04'].inBand, false, 'край полосы рисуется скруглением, а не серединой');
    assert.equal(cells['2026-11-10'].inBand, false);
    assert.equal(cells['2026-11-05'].inBand, true);
});
