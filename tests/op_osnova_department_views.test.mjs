/*
 * IC-1 (#470): «Основа ОП» — расчёт ресурсов и аукцион смен отдела продаж.
 *
 * Руководство отдела (глава и СВ) получает «Расчет ресурсов → Основа ОП» и аукцион; из
 * рядовых — только операторы «Основы ОП» (модель направления op_osnova): прогон аукциона
 * есть лишь у неё, а «Яндекс Регистрация», «Поток» и верификаторы в нём не участвуют.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { departmentAllowsView } from '../src/utils/departmentViews.js';
import { isShiftAuctionDirection } from '../src/components/resources/shiftAuctionParticipants.js';

const salesOperator = (directionModel) => ({
  id: 9001, role: 'operator', department_code: 'op', department_id: 367, direction_model: directionModel,
});

test('аукцион смен — операторам «Основы ОП» и только им', () => {
  assert.equal(departmentAllowsView(salesOperator('op_osnova'), 'shift_auction'), true);
  for (const model of ['op_yandex_reg', 'op_potok', 'op_verificator', '', null]) {
    assert.equal(departmentAllowsView(salesOperator(model), 'shift_auction'), false, String(model));
  }
  // Остальной набор оператора ОП прежний.
  assert.equal(departmentAllowsView(salesOperator('op_osnova'), 'work_schedules'), true);
  assert.equal(departmentAllowsView(salesOperator('op_osnova'), 'resource_fte_op'), false);
});

test('стажёру «Основы» аукцион не открывается: смены выбирает оператор', () => {
  assert.equal(departmentAllowsView({ ...salesOperator('op_osnova'), role: 'trainee' }, 'shift_auction'), false);
});

test('СВ и глава отдела продаж видят расчёт «Основы ОП» и аукцион', () => {
  const sv = { id: 9002, role: 'sv', department_code: 'op', department_id: 367 };
  assert.equal(departmentAllowsView(sv, 'resource_fte_op'), true);
  assert.equal(departmentAllowsView(sv, 'shift_auction'), true);
  // Линия и чат СЗоВ — не их раздел.
  assert.equal(departmentAllowsView(sv, 'resource_fte'), false);
  const head = {
    id: 313, role: 'admin', department_code: 'op', department_id: 367,
    headed_department_id: 367, headed_department_ids: [367], headed_department_code: 'op', headed_department_codes: ['op'],
  };
  assert.equal(departmentAllowsView(head, 'resource_fte_op'), true);
  assert.equal(departmentAllowsView(head, 'shift_auction'), true);
});

test('участник аукциона «Основы ОП» — по точному имени направления', () => {
  assert.equal(isShiftAuctionDirection('Основа ОП', 'op_osnova'), true);
  assert.equal(isShiftAuctionDirection('  основа   оп ', 'op_osnova'), true);
  // «Основа» СЗоВ — другой прогон, и наоборот.
  assert.equal(isShiftAuctionDirection('Основа', 'op_osnova'), false);
  assert.equal(isShiftAuctionDirection('Основа ОП', 'line'), false);
});
