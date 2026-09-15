import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BILLING_GROUPING_AR_ALERT,
  BILLING_GROUPING_COLUMNS,
  billingGroupingCommentBlocks,
  billingGroupingPercent,
  billingGroupingRow,
} from '../src/components/resources/billingGrouping.js';

// Строки взяты из таблицы владельца, по которой сделана вкладка «Группировка».
const hour = (h, arrived, served, extra = {}) => ({
  hour: h,
  arrived,
  served,
  lost: arrived - served,
  talk_seconds: 0,
  wait_ok_seconds: 0,
  ...extra,
});

test('колонки и их порядок — как в таблице владельца', () => {
  assert.deepEqual(BILLING_GROUPING_COLUMNS.map((column) => column.label), [
    'С', 'Получено', 'Принято', 'Потеряно', '% Неотв', 'Средн. Прод.',
    'Средн. время ожидания', 'Прогноз смен', 'Запланировано смен', 'Факт смен',
    'Разница факта от прогноза', 'Комментарии',
  ]);
});

test('процент неотвеченных целый и совпадает с таблицей владельца', () => {
  const cases = [
    [29, 18, '38%'], [13, 9, '31%'], [8, 7, '13%'], [7, 5, '29%'], [50, 48, '4%'],
    [64, 63, '2%'], [114, 97, '15%'], [205, 42, '80%'], [183, 32, '83%'], [50, 50, '0%'],
  ];
  for (const [arrived, served, expected] of cases) {
    assert.equal(billingGroupingPercent(billingGroupingRow(hour(0, arrived, served)).ar), expected,
      `${arrived}/${served}`);
  }
});

test('заливка: 4 % и ровно порог — без неё, 8 % — залито', () => {
  assert.equal(BILLING_GROUPING_AR_ALERT, 0.05);
  assert.equal(billingGroupingRow(hour(8, 50, 48)).arAlert, false);
  assert.equal(billingGroupingRow(hour(9, 20, 19)).arAlert, false);
  assert.equal(billingGroupingRow(hour(14, 123, 113)).arAlert, true);
});

test('средние — целые секунды усечением, от принятых звонков', () => {
  const row = billingGroupingRow(hour(3, 2, 2, { talk_seconds: 1343, wait_ok_seconds: 1 }));
  assert.equal(row.talk, 671);
  assert.equal(row.wait, 0);
});

test('без звонков — прочерки, а не нули', () => {
  const row = billingGroupingRow(hour(4, 0, 0));
  assert.equal(row.ar, null);
  assert.equal(billingGroupingPercent(row.ar), '—');
  assert.equal(row.talk, null);
  assert.equal(row.wait, null);
  assert.equal(row.arAlert, false);
});

test('разница — факт минус прогноз, а не минус план', () => {
  const row = billingGroupingRow(hour(15, 180, 63, { forecast: 10, planned: 6, fact: 14 }));
  assert.equal(row.delta, 4);
  assert.equal(billingGroupingRow(hour(15, 1, 1, { forecast: 5, planned: 9, fact: 3 })).delta, -2);
});

test('нет данных о сменах — разница не считается, ноль смен остаётся нулём', () => {
  const noFact = billingGroupingRow(hour(22, 1, 1, { forecast: 5, planned: 1, fact: null }));
  assert.equal(noFact.fact, null);
  assert.equal(noFact.delta, null);
  const noForecast = billingGroupingRow(hour(22, 1, 1, { forecast: null, planned: 0, fact: 0 }));
  assert.equal(noForecast.delta, null);
  assert.equal(noForecast.planned, 0);
  assert.equal(noForecast.fact, 0);
});

test('соседние часы с одним комментарием — одна ячейка, пустые не склеиваются', () => {
  const said = 'Во время поступления звонка оператор находился в разговоре';
  const blocks = billingGroupingCommentBlocks([
    { hour: 0, comment: said },
    { hour: 1, comment: said },
    { hour: 2, comment: ` ${said} ` },
    { hour: 3, comment: '' },
    { hour: 4, comment: '' },
    { hour: 5, comment: said },
    { hour: 6, comment: 'Были выделены операторы для обзвона СМЗ' },
  ]);
  assert.deepEqual(blocks.map(({ hourFrom, hourTo, span, index }) => [hourFrom, hourTo, span, index]), [
    [0, 2, 3, 0],
    [3, 3, 1, 3],
    [4, 4, 1, 4],
    [5, 5, 1, 5],
    [6, 6, 1, 6],
  ]);
});

test('одинаковый текст через разрыв в часах не склеивается', () => {
  const blocks = billingGroupingCommentBlocks([
    { hour: 8, comment: 'СМЗ' },
    { hour: 10, comment: 'СМЗ' },
  ]);
  assert.equal(blocks.length, 2);
});
