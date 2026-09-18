import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BILLING_OPERATOR_DIRECTIONS,
  billingOperatorDirectionReport,
  billingOperatorDirectionRow,
  billingOperatorHasDialWait,
} from '../src/components/resources/billingOperatorDirection.js';

// Строка ответа ручки oktell_billing_operators: обе половины сразу.
const operator = (name, extra = {}) => ({
  operator: name,
  served_in: 0,
  served_out: 0,
  call_in_seconds: 0,
  call_out_seconds: 0,
  handle_in_seconds: 0,
  handle_out_seconds: 0,
  talk_in_seconds: 0,
  talk_out_seconds: 0,
  postproc_in_seconds: 0,
  postproc_out_seconds: 0,
  hold_in_seconds: 0,
  hold_out_seconds: 0,
  dial_in_seconds: 0,
  dial_out_seconds: 0,
  dial_other_seconds: 0,
  dial_wait_out_seconds: 0,
  wait_seconds: 0,
  pause_seconds: 0,
  ...extra,
});

const IVANOVA = operator('Иванова А.', {
  served_in: 30,
  call_in_seconds: 6000,
  handle_in_seconds: 9000,
  served_out: 4,
  call_out_seconds: 200,
  handle_out_seconds: 200,
  talk_in_seconds: 5900,
  talk_out_seconds: 180,
  postproc_in_seconds: 100,
  postproc_out_seconds: 30,
  hold_in_seconds: 50,
  dial_in_seconds: 20,
  dial_out_seconds: 5,
  dial_other_seconds: 10,
  wait_seconds: 3000,
  pause_seconds: 1000,
});

const PETROV = operator('Петров Б.', {
  served_out: 12,
  call_out_seconds: 2100,
  handle_out_seconds: 2100,
  talk_out_seconds: 2000,
  postproc_out_seconds: 40,
  dial_out_seconds: 100,
  dial_wait_out_seconds: 60,
  wait_seconds: 500,
});

const REPORT = {
  date_from: '2026-07-01',
  date_to: '2026-07-02',
  days: [
    { date: '2026-07-01', operators: [IVANOVA, PETROV] },
    { date: '2026-07-02', operators: [operator('Иванова А.', { served_in: 20, call_in_seconds: 4000, talk_in_seconds: 3800 })] },
  ],
  operators: [IVANOVA, PETROV],
};

test('переключатель — только «Вход» и «Исход»', () => {
  assert.deepEqual(BILLING_OPERATOR_DIRECTIONS.map((item) => item.key), ['incoming', 'outgoing']);
  assert.deepEqual(BILLING_OPERATOR_DIRECTIONS.map((item) => item.label), ['Вход', 'Исход']);
});

test('строка берёт половину своего направления', () => {
  const incoming = billingOperatorDirectionRow(IVANOVA, 'incoming');
  assert.equal(incoming.served, 30);
  assert.equal(incoming.talk_seconds, 6000);
  // Разговор и всё время обработки — разные величины: ATT от первого, AHT от второго.
  assert.equal(incoming.handle_seconds, 9000);
  assert.equal(incoming.talk_state_seconds, 5900);
  assert.equal(incoming.postproc_seconds, 100);
  assert.equal(incoming.hold_seconds, 50);

  const outgoing = billingOperatorDirectionRow(IVANOVA, 'outgoing');
  assert.equal(outgoing.served, 4);
  assert.equal(outgoing.talk_seconds, 200);
  assert.equal(outgoing.talk_state_seconds, 180);
  assert.equal(outgoing.postproc_seconds, 30);
});

test('дозвон показывается только на исходящих', () => {
  assert.equal(billingOperatorDirectionRow(PETROV, 'incoming').dial_wait_seconds, 0);
  assert.equal(billingOperatorDirectionRow(PETROV, 'outgoing').dial_wait_seconds, 60);
});

test('занятость считается по дню целиком, а не по направлению', () => {
  // «Готов» и «Перерыв» в Oktell направления не знают: если делить активное время,
  // OCC на «Исходе» показал бы занятость в разы ниже фактической.
  const incoming = billingOperatorDirectionRow(IVANOVA, 'incoming');
  const outgoing = billingOperatorDirectionRow(IVANOVA, 'outgoing');
  assert.equal(incoming.active_seconds, outgoing.active_seconds);
  assert.equal(incoming.active_seconds, 5900 + 180 + 100 + 30 + 50 + 20 + 5 + 10);
  assert.equal(incoming.wait_seconds, 3000);
  assert.equal(incoming.pause_seconds, 1000);
});

test('оператор без работы в направлении из таблицы выпадает, итоги пересчитываются', () => {
  const incoming = billingOperatorDirectionReport(REPORT, 'incoming');
  assert.deepEqual(incoming.operators.map((row) => row.operator), ['Иванова А.']);
  assert.equal(incoming.totals.served, 30);
  assert.equal(incoming.totals.talk_state_seconds, 5900);

  const outgoing = billingOperatorDirectionReport(REPORT, 'outgoing');
  assert.deepEqual(outgoing.operators.map((row) => row.operator), ['Петров Б.', 'Иванова А.']);
  assert.equal(outgoing.totals.served, 16);
  assert.equal(outgoing.totals.talk_state_seconds, 2180);
});

test('день без звонков направления в списке дней не остаётся', () => {
  const outgoing = billingOperatorDirectionReport(REPORT, 'outgoing');
  assert.deepEqual(outgoing.days.map((day) => day.date), ['2026-07-01']);
  const incoming = billingOperatorDirectionReport(REPORT, 'incoming');
  assert.deepEqual(incoming.days.map((day) => day.date), ['2026-07-01', '2026-07-02']);
});

test('период и окно времени из ответа не теряются', () => {
  const incoming = billingOperatorDirectionReport(REPORT, 'incoming');
  assert.equal(incoming.date_from, '2026-07-01');
  assert.equal(incoming.date_to, '2026-07-02');
});

test('колонка «Дозвон» включается только когда станция его пишет', () => {
  assert.equal(billingOperatorHasDialWait(billingOperatorDirectionReport(REPORT, 'outgoing')), true);
  assert.equal(billingOperatorHasDialWait(billingOperatorDirectionReport(REPORT, 'incoming')), false);
  const noDial = { ...REPORT, days: [], operators: [IVANOVA] };
  assert.equal(billingOperatorHasDialWait(billingOperatorDirectionReport(noDial, 'outgoing')), false);
});
