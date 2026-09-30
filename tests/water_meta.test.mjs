import test from 'node:test';
import assert from 'node:assert/strict';

import {
  EXPORT_MAX_DAYS,
  KIND_LABELS,
  TARIFF_LABELS,
  blocksWord,
  daysWord,
  defaultOfficeId,
  exportFileName,
  fmtAvg,
  forecastLabel,
  nextIssueLine,
  parseCount,
  plural,
  stockStatus,
  tariffLabel,
  verdictBasis,
  verdictHeadline,
} from '../src/components/water/waterMeta.js';

test('plural follows Russian rules', () => {
  assert.equal(blocksWord(1), '1 блок');
  assert.equal(blocksWord(3), '3 блока');
  assert.equal(blocksWord(11), '11 блоков');
  assert.equal(blocksWord(21), '21 блок');
  assert.equal(daysWord(14), '14 дней');
  assert.equal(plural(102, 'a', 'b', 'c'), 'b');
});

test('tariff codes are Fleet codes, Business and Ultima are labelled', () => {
  assert.equal(tariffLabel('business'), 'Business');
  assert.equal(tariffLabel('ultimate'), 'Premier (Ultima)');
  assert.equal(tariffLabel('comfort'), 'Комфорт');
  assert.equal(tariffLabel('sdd_int'), 'sdd_int');
  assert.ok(Object.keys(TARIFF_LABELS).includes('comfort_plus'));
});

test('kind labels are the TZ words', () => {
  assert.deepEqual(KIND_LABELS, { welcome: 'Приветственная', activity: 'За активность' });
});

test('export file name mirrors report_filename', () => {
  assert.equal(exportFileName('2026-09-01', '2026-09-30'), 'Выдачи воды 01.09.2026 — 30.09.2026.xlsx');
  assert.equal(exportFileName('2026-09-30', '2026-09-30'), 'Выдачи воды 30.09.2026.xlsx');
  assert.equal(exportFileName('', ''), 'Выдачи воды.xlsx');
  assert.equal(EXPORT_MAX_DAYS, 366);
});

test('enough stock is neutral, low and buy are coloured', () => {
  assert.match(stockStatus('enough').pill, /slate/);
  assert.match(stockStatus('low').pill, /amber/);
  assert.match(stockStatus('buy').pill, /rose/);
  assert.equal(stockStatus('unknown').label, 'Достаточно');
});

test('verdict wording', () => {
  assert.equal(verdictHeadline({ allowed: true, kind: 'welcome' }), 'Можно выдать приветственный блок');
  assert.equal(verdictHeadline({ allowed: true, kind: 'activity' }), 'Можно выдать воду');
  assert.equal(verdictHeadline({ allowed: false }), 'Воду выдать нельзя');
  assert.equal(verdictBasis({ allowed: true, kind: 'activity', trips: 34, trips_basis: 'since_last' }),
    '34 поездки с прошлой выдачи');
  assert.equal(verdictBasis({ allowed: true, kind: 'activity', trips: 25, trips_basis: 'week' }),
    '25 поездок за последние 7 дней');
  assert.equal(verdictBasis({ allowed: false }), '');
  assert.equal(verdictBasis({ allowed: true, kind: 'welcome', fk: 'passed' }),
    'Новый водитель — ещё ни одного выполненного заказа · ФК пройден');
  assert.equal(verdictBasis({ allowed: true, kind: 'welcome', fk: 'unknown' }),
    'Новый водитель — ещё ни одного выполненного заказа');
  assert.equal(nextIssueLine({ next_date: '2026-10-02', last_issue: { id: 1 } }),
    'Следующая выдача за активность — с 02.10.2026');
  assert.equal(nextIssueLine({ next_date: null }), '');
});

test('forecast and averages', () => {
  // «Требуется закупка» уже сказано плашкой в той же строке — прогноз молчит.
  assert.equal(forecastLabel({ status: 'buy', days_to_buy: 0 }), '—');
  assert.equal(forecastLabel({ status: 'low', days_to_buy: 3 }), 'через ~3 дня');
  assert.equal(forecastLabel({ status: 'enough', days_to_buy: null }), '—');
  assert.equal(fmtAvg(0), '0');
  assert.equal(fmtAvg(1.25), '1,3');
  assert.equal(fmtAvg(12.4), '12');
});

test('parseCount accepts only whole non-negative numbers', () => {
  assert.equal(parseCount(''), null);
  assert.equal(parseCount(' 12 '), 12);
  assert.equal(parseCount(0), 0);
  assert.ok(Number.isNaN(parseCount('-1')));
  assert.ok(Number.isNaN(parseCount('1.5')));
  assert.ok(Number.isNaN(parseCount('два')));
});

test('default office: remembered, then the only one in own city, then the only one', () => {
  const offices = [
    { id: 1, city: 'Алматы', is_active: true },
    { id: 2, city: 'Алматы', is_active: true },
    { id: 3, city: 'Шымкент', is_active: true },
    { id: 4, city: 'Шымкент', is_active: false },
  ];
  assert.equal(defaultOfficeId(offices, 'Алматы', 2), 2);
  assert.equal(defaultOfficeId(offices, 'Шымкент', 4), 3);
  assert.equal(defaultOfficeId(offices, 'Алматы', null), null);
  assert.equal(defaultOfficeId([offices[2]], null, null), 3);
});
