/*
 * «Аналитика» журнала оценок за ПЕРИОД (задача #389): правила и календарь.
 *
 * Правила — src/call_evaluation/analyticsPeriod.js: период по умолчанию, месяц
 * для того, что живёт по месяцам (выгрузка, журнал оператора, «ОС от ИИ»), и
 * параметры запроса. Календарь — EvalPeriodPicker: своя разметка журнала (в его
 * сборке нет Tailwind), правила — общие с календарём ИИ-оценки.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
    analyticsDataView, analyticsMonthPeriod, analyticsPeriodMonth, analyticsPeriodQuery, currentMonthPeriod,
} from '../src/call_evaluation/analyticsPeriod.js';
import { dayCells, pickDayStep, selectionBand } from '../src/components/ui/periodRange.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');

const NOW = new Date(2026, 10, 10, 2, 30);   // 10 ноября 2026, 02:30 по местному

test('период по умолчанию — текущий месяц по местному времени, а не по UTC', () => {
    assert.deepEqual(currentMonthPeriod(NOW), { from: '2026-11-01', to: '2026-11-30' });
    // 1-е число, ночь: по UTC это ещё прошлый месяц — по местному уже новый.
    assert.deepEqual(currentMonthPeriod(new Date(2026, 11, 1, 1, 0)), { from: '2026-12-01', to: '2026-12-31' });
    assert.deepEqual(analyticsMonthPeriod('2024-02'), { from: '2024-02-01', to: '2024-02-29' });
    assert.equal(analyticsMonthPeriod('2026-13'), null);
    assert.equal(analyticsMonthPeriod(''), null);
});

test('месяц для помесячного: целый месяц — он сам, иначе месяц конца, не позже текущего', () => {
    assert.equal(analyticsPeriodMonth({ from: '2026-09-01', to: '2026-09-30' }, NOW), '2026-09');
    assert.equal(analyticsPeriodMonth({ from: '2026-11-04', to: '2026-11-10' }, NOW), '2026-11');
    assert.equal(analyticsPeriodMonth({ from: '2026-08-20', to: '2026-09-05' }, NOW), '2026-09');
    assert.equal(analyticsPeriodMonth({ from: '2025-01-01', to: '2025-12-31' }, NOW), '2025-12');
    assert.equal(analyticsPeriodMonth({ from: '2026-01-01', to: '2026-12-31' }, NOW), '2026-11',
        'год, который ещё идёт, — текущий месяц, а не будущий декабрь');
    assert.equal(analyticsPeriodMonth({ from: '', to: '' }, NOW), '2026-11');
});

test('запрос: даты периода или period=all', () => {
    assert.deepEqual(analyticsPeriodQuery({ from: '2026-11-04', to: '2026-11-10' }),
        { date_from: '2026-11-04', date_to: '2026-11-10' });
    assert.deepEqual(analyticsPeriodQuery({ from: '', to: '' }), { period: 'all' });
    assert.deepEqual(analyticsPeriodQuery(null), { period: 'all' });
    // Период с одним краем календарь не даёт; если он всё же пришёл — «Весь период», а не
    // полупустой запрос, на который сервер ответил бы 400.
    assert.deepEqual(analyticsPeriodQuery({ from: '2026-11-04', to: '' }), { period: 'all' });
});

test('сетка дней: пустые до 1-го, шесть недель с fill, края и полоса выбора', () => {
    // Октябрь 2026 начинается в четверг: три пустые клетки впереди.
    const cells = dayCells(2026, 9, { lo: '2026-10-05', hi: '2026-10-09', max: '2026-10-20', today: '2026-10-07', fill: true });
    assert.equal(cells.length, 42);
    assert.deepEqual(cells.slice(0, 3).map((c) => c.iso), [null, null, null]);
    const byIso = Object.fromEntries(cells.filter((c) => c.iso).map((c) => [c.iso, c]));
    assert.ok(byIso['2026-10-05'].isEnd && byIso['2026-10-05'].bandStart);
    assert.ok(byIso['2026-10-07'].inBand && byIso['2026-10-07'].today);
    assert.ok(byIso['2026-10-09'].isEnd && byIso['2026-10-09'].bandEnd);
    assert.equal(byIso['2026-10-21'].disabled, true);
    assert.equal(dayCells(2026, 9).length, 34, 'без fill — только месяц');
});

test('выбор дня: начало, конец, тот же день дважды — один день; полоса тянется за курсором', () => {
    const first = pickDayStep({ from: '', to: '' }, 0, '2026-11-10');
    assert.deepEqual(first, { draft: { from: '2026-11-10', to: '2026-11-10' }, step: 1, done: null });
    assert.deepEqual(pickDayStep(first.draft, 1, '2026-11-10').done, { from: '2026-11-10', to: '2026-11-10' });
    assert.deepEqual(pickDayStep(first.draft, 1, '2026-11-04').done, { from: '2026-11-04', to: '2026-11-10' });
    assert.deepEqual(selectionBand(first.draft, 1, '2026-11-04'), { lo: '2026-11-04', hi: '2026-11-10', hasRange: true });
    assert.deepEqual(selectionBand({ from: '2026-11-01', to: '2026-11-03' }, 0, '2026-11-20'),
        { lo: '2026-11-01', hi: '2026-11-03', hasRange: true }, 'вне шага «конец» курсор полосу не тянет');
});

test('таблица делится по периоду ответа: пока грузится неделя, строки октября — помесячно и с планом', () => {
    const october = { requested_month: '2026-10', requested_period: { from: '2026-10-01', to: '2026-10-31' },
                      plan_available: true, operators: [] };
    const week = { from: '2026-11-04', to: '2026-11-10' };
    assert.deepEqual(analyticsDataView(october, week),
        { dataMonth: '2026-10', tabsContext: { month: '2026-10' }, planAvailable: true });
});

test('ответ не за один месяц — вкладки по датам ответа и без плана, «% нормы» и «Уведомить»', () => {
    const weekly = { requested_month: null, requested_period: { from: '2026-11-04', to: '2026-11-10' },
                     plan_available: false };
    assert.deepEqual(analyticsDataView(weekly, { from: '2026-10-01', to: '2026-10-31' }),
        { dataMonth: null, tabsContext: { period: { from: '2026-11-04', to: '2026-11-10' } }, planAvailable: false });
    const all = { requested_month: null, requested_period: { from: '', to: '' }, plan_available: false };
    assert.deepEqual(analyticsDataView(all, null).tabsContext, { period: { from: '', to: '' } });
});

test('ответ старого сервера (без plan_available) — с планом, как было; ответа нет — по выбранному', () => {
    assert.equal(analyticsDataView({ requested_month: '2026-10' }, null).planAvailable, true);
    assert.deepEqual(analyticsDataView(null, { from: '2026-10-01', to: '2026-10-31' }),
        { dataMonth: '2026-10', tabsContext: { month: '2026-10' }, planAvailable: true });
    assert.deepEqual(analyticsDataView(null, { from: '2026-11-04', to: '2026-11-10' }),
        { dataMonth: null, tabsContext: { period: { from: '2026-11-04', to: '2026-11-10' } }, planAvailable: false });
});

/* ── календарь журнала ─────────────────────────────────────────────────── */

async function loadPicker() {
    const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
    mkdirSync(dir, { recursive: true });
    const outfile = join(dir, 'EvalPeriodPicker.mjs');
    buildSync({
        entryPoints: [fileURLToPath(new URL('../src/call_evaluation/EvalPeriodPicker.jsx', import.meta.url))],
        bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
        packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' }, logLevel: 'silent',
    });
    return import(`file://${outfile.replace(/\\/g, '/')}`);
}

const { default: EvalPeriodPicker, PeriodCalendar } = await loadPicker();
const chip = (props) => renderToStaticMarkup(React.createElement(EvalPeriodPicker, { onChange() {}, ...props }));

test('кнопка календаря журнала: подпись периода словами, вид кнопки журнала', () => {
    const month = chip({ from: '2026-11-01', to: '2026-11-30' });
    assert.ok(month.includes('Ноябрь 2026'));
    assert.ok(month.includes('ce-select-btn ce-period-btn'), 'та же кнопка, что у EvalSelect');
    assert.ok(month.includes('aria-haspopup="dialog"'));
    assert.ok(chip({ from: '', to: '' }).includes('Весь период'));
    assert.ok(chip({ from: '2026-01-01', to: '2026-12-31' }).includes('2026 год'));
    assert.ok(chip({ from: '2026-11-10', to: '2026-11-10' }).includes('10 ноя 2026'));
    assert.ok(!month.includes('role="dialog"'), 'закрытый календарь панели не рисует');
});

/* Панель (PeriodCalendar) — в портале, его react-dom/server не рисует; рисуем её
   саму. Разбор #389 (тест ревьюера): годы до начала журнала не предлагаются, за
   границы не листается, шесть недель всегда. */
const panel = (props) => renderToStaticMarkup(React.createElement(PeriodCalendar, {
    min: '2025-01-01', max: '2026-11-10', onPick() {}, ...props }));
const buttons = (html, cls) => [...html.matchAll(new RegExp(`<button[^>]*class="${cls}[^"]*"[^>]*>([^<]*)<`, 'g'))]
    .map((m) => ({ tag: m[0], text: m[1] }));

test('панель журнала, год: вид «Годы», годов до начала журнала нет', () => {
    const html = panel({ from: '2025-01-01', to: '2025-12-31' });
    assert.match(html, /aria-selected="true"[^>]*>Годы</);
    const years = buttons(html, 'ce-period-whole');
    assert.deepEqual(years.map((b) => b.text), ['2025', '2026']);
    assert.ok(years.find((b) => b.text === '2025').tag.includes('aria-pressed="true"'));
    assert.match(html, /aria-label="Более ранние годы" disabled=""/);
});

test('панель журнала, месяц: вид «Месяцы»; назад за начало журнала не листается, будущее недоступно', () => {
    const html = panel({ from: '2025-01-01', to: '2025-01-31' });
    assert.match(html, /aria-selected="true"[^>]*>Месяцы</);
    assert.match(html, /aria-label="Предыдущий год" disabled=""/);
    const now = panel({ from: '2026-11-01', to: '2026-11-30' });
    const dec = buttons(now, 'ce-period-whole').find((b) => /дек/i.test(b.text));
    assert.ok(dec && dec.tag.includes('disabled=""'), 'декабрь 2026 позже max');
});

test('панель журнала, дни: шесть недель, дни после max недоступны, быстрые периоды 2 × 2', () => {
    const html = panel({ from: '2026-11-10', to: '2026-11-10' });
    assert.match(html, /aria-selected="true"[^>]*>Дни</);
    assert.equal((html.match(/class="ce-period-cell/g) || []).length, 42);
    const days = buttons(html, 'ce-period-day');
    assert.ok(days.find((b) => b.text === '11').tag.includes('disabled=""'), '11 ноября позже max');
    assert.ok(!days.find((b) => b.text === '10').tag.includes('disabled=""'));
    assert.equal((html.match(/class="ce-period-preset[ "]/g) || []).length, 4);
});

