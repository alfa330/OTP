import test from 'node:test';
import assert from 'node:assert/strict';

import {
  TASK_DEPARTMENT_ALL,
  TASK_DEPARTMENT_NONE,
  buildDepartmentOptions,
  departmentQueryParams,
  departmentValueOf,
  mergeSelectedDepartment,
  normalizeDepartmentValue,
  resolveInitialDepartment,
} from '../src/components/tasks/departmentFilter.js';
import { boardQueryParams, scopeQueryParams } from '../src/components/tasks/boardQuery.js';

const DEPARTMENTS = [
  { id: 12, name: 'СЗоВ', task_count: 40 },
  { id: 3, name: 'Отдел продаж', task_count: 7 },
  { id: null, name: null, task_count: 2 },
];

test('«Все отделы» — это отсутствие фильтра, а не значение для сервера', () => {
  assert.deepEqual(departmentQueryParams(TASK_DEPARTMENT_ALL), {});
  assert.deepEqual(departmentQueryParams(null), {});
  assert.deepEqual(departmentQueryParams(undefined), {});
  assert.deepEqual(departmentQueryParams(''), {});
});

test('отдел и «без отдела» уходят в department_id', () => {
  assert.deepEqual(departmentQueryParams(12), { department_id: '12' });
  assert.deepEqual(departmentQueryParams('12'), { department_id: '12' });
  assert.deepEqual(departmentQueryParams(TASK_DEPARTMENT_NONE), { department_id: 'none' });
});

test('мусор в значении не уезжает в запрос', () => {
  // Значение приходит из localStorage — там может лежать что угодно.
  assert.equal(normalizeDepartmentValue('чепуха'), TASK_DEPARTMENT_ALL);
  assert.equal(normalizeDepartmentValue('-4'), TASK_DEPARTMENT_ALL);
  assert.equal(normalizeDepartmentValue('0'), TASK_DEPARTMENT_ALL);
  assert.deepEqual(departmentQueryParams('чепуха'), {});
});

test('строка без отдела распознаётся по пустому id, а не по имени', () => {
  assert.equal(departmentValueOf({ id: null, name: null }), TASK_DEPARTMENT_NONE);
  assert.equal(departmentValueOf({ id: undefined }), TASK_DEPARTMENT_NONE);
  assert.equal(departmentValueOf({ id: 7, name: 'IT' }), '7');
});

test('в списке «Все отделы» сверху, «Без отдела» снизу, остальные по алфавиту', () => {
  const options = buildDepartmentOptions(DEPARTMENTS);
  assert.deepEqual(options.map((option) => option.value), [TASK_DEPARTMENT_ALL, '3', '12', TASK_DEPARTMENT_NONE]);
  assert.equal(options[0].label, 'Все отделы');
  assert.equal(options.at(-1).label, 'Без отдела');
});

test('«Без отдела» не показываем, когда таких задач нет', () => {
  const options = buildDepartmentOptions(DEPARTMENTS.filter((item) => item.id !== null));
  assert.equal(options.some((option) => option.value === TASK_DEPARTMENT_NONE), false);
});

test('по умолчанию выбран свой отдел, а сохранённый выбор его перебивает', () => {
  assert.equal(resolveInitialDepartment(DEPARTMENTS, 12, ''), '12');
  assert.equal(resolveInitialDepartment(DEPARTMENTS, 12, '3'), '3');
  assert.equal(resolveInitialDepartment(DEPARTMENTS, 12, TASK_DEPARTMENT_ALL), TASK_DEPARTMENT_ALL);
  assert.equal(resolveInitialDepartment(DEPARTMENTS, 12, TASK_DEPARTMENT_NONE), TASK_DEPARTMENT_NONE);
});

test('несуществующий отдел не выбирается — иначе раздел откроется пустым', () => {
  // Сохранённый выбор устаревает, а сервер мог не прислать отдел по умолчанию.
  assert.equal(resolveInitialDepartment(DEPARTMENTS, 12, '999'), '12');
  assert.equal(resolveInitialDepartment(DEPARTMENTS, 999, ''), TASK_DEPARTMENT_ALL);
  assert.equal(resolveInitialDepartment(DEPARTMENTS, null, ''), TASK_DEPARTMENT_ALL);
  // Список не загрузился — сужать выборку нечем, показываем всё.
  assert.equal(resolveInitialDepartment([], 12, '12'), TASK_DEPARTMENT_ALL);
});

test('выбранный отдел не пропадает из списка, даже если задач в нём не осталось', () => {
  const refreshed = DEPARTMENTS.filter((item) => item.id !== 3);
  const merged = mergeSelectedDepartment(refreshed, DEPARTMENTS, '3');
  assert.equal(merged.some((item) => item.id === 3), true);
  assert.equal(merged.find((item) => item.id === 3).task_count, 0);
  // Всё остальное список не раздувает.
  assert.equal(mergeSelectedDepartment(refreshed, DEPARTMENTS, TASK_DEPARTMENT_ALL).length, refreshed.length);
  assert.equal(mergeSelectedDepartment(refreshed, DEPARTMENTS, '12').length, refreshed.length);
});

test('отдел уходит в каждый запрос доски и в выгрузку', () => {
  const column = boardQueryParams({
    scope: 'all', mode: 'board', column: 'todo', limit: 20, offset: 0, departmentId: '12',
  });
  assert.equal(column.department_id, '12');
  assert.equal(column.status, 'assigned');

  // Охват выгрузки обязан совпадать с доской, иначе в Excel уедет лишний отдел.
  ['my', 'assigned', 'all', 'person:57'].forEach((scope) => {
    const board = boardQueryParams({ scope, mode: 'board', column: 'todo', limit: 20, offset: 0, departmentId: '12' });
    assert.deepEqual(scopeQueryParams(scope, '12').department_id, board.department_id, scope);
  });

  // «Все отделы» ничего не добавляет ни туда, ни туда.
  assert.equal(boardQueryParams({ scope: 'all', mode: 'board', limit: 20, offset: 0 }).department_id, undefined);
  assert.deepEqual(scopeQueryParams('all', TASK_DEPARTMENT_ALL), {});
});
