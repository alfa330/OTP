/*
 * Люди доски в форме задачи (владелец 24.11.2026): «по каждой доске своя привязка —
 * можно выбрать только тех, кто есть в проекте как исполнитель», «выбрать всех либо
 * конкретных людей» и «если человека нет в данном проекте, зачем выводить его в
 * исполнителях». Люди доски — отмеченные исполнители, а без отметок — весь состав.
 * Сервер держит то же правило (tests/test_task_project_executors.py).
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  allBoardAssigneeIds,
  assigneesAfterProjectChange,
  boardAssignees,
  hasBoardAssignees,
  isAllBoardAssigneesSelection,
  keepAssigneesForProject,
  projectExecutors,
} from '../src/components/tasks/taskProjects.js';

const ME = 1;
// Люди выдуманы. HR: два исполнителя и постановщик-исполнитель, плюс участник без отметки.
const HR = {
  id: 10,
  code: 'HR',
  members: [
    { id: 4, name: 'Ясенева Яна', access_level: 'member', is_executor: true },
    { id: 3, name: 'Аралова Ая', access_level: 'board', is_executor: true },
    { id: ME, name: 'Постановщик Пётр', access_level: 'member', is_executor: true },
    { id: 9, name: 'Наблюдатель Нина', access_level: 'board', is_executor: false },
  ],
};
// Доска без отметок: люди доски — весь её состав.
const FIN = { id: 20, code: 'FIN', members: [{ id: 7, name: 'Финов Фёдор', access_level: 'member' }] };
// Один исполнитель — выбирать «всех» не из кого.
const ACC = { id: 30, code: 'ACC', members: [{ id: 5, name: 'Бухова Бэла', access_level: 'member', is_executor: true }] };
// Доска без людей вовсе — поручают как раньше.
const EMPTY = { id: 40, code: 'BE', members: [] };

test('люди доски — отмеченные исполнители, а без отметок весь состав; без людей — как раньше', () => {
  // По имени: Аралова, Постановщик, Ясенева. Наблюдатель Нина в составе, но не исполнитель.
  assert.deepEqual(projectExecutors(HR).map((person) => person.id), [3, ME, 4]);
  assert.deepEqual(boardAssignees(HR).map((person) => person.id), [3, ME, 4]);
  // «Если человека нет в данном проекте, зачем выводить его в исполнителях»: без отметок — состав.
  assert.deepEqual(boardAssignees(FIN).map((person) => person.id), [7]);
  assert.equal(hasBoardAssignees(HR), true);
  assert.equal(hasBoardAssignees(FIN), true);
  assert.equal(hasBoardAssignees(EMPTY), false);
  assert.equal(hasBoardAssignees(null), false);
  assert.deepEqual(projectExecutors(undefined), []);
});

test('«Все» — каждый из людей доски, кроме постановщика; у продакшна — только разрешённые', () => {
  assert.deepEqual(allBoardAssigneeIds(HR, { currentUserId: ME }), ['3', '4']);
  assert.deepEqual(allBoardAssigneeIds(HR, { currentUserId: 99 }), ['3', String(ME), '4']);
  assert.deepEqual(allBoardAssigneeIds(HR, { currentUserId: ME, allowedIds: new Set(['4']) }), ['4']);
  assert.deepEqual(allBoardAssigneeIds(FIN, { currentUserId: ME }), ['7']);
  assert.deepEqual(allBoardAssigneeIds(EMPTY, { currentUserId: ME }), []);
});

test('при смене доски остаются только люди новой доски, у доски без людей — все', () => {
  assert.deepEqual(keepAssigneesForProject(['3', '7', '9'], HR), ['3']);
  assert.deepEqual(keepAssigneesForProject(['3', '7'], FIN), ['7']);
  assert.deepEqual(keepAssigneesForProject(['3', '7'], EMPTY), ['3', '7']);
  assert.deepEqual(keepAssigneesForProject(undefined, HR), []);
});

test('правка открывается на «Все», только если в задаче ровно все люди доски (себя не считаем)', () => {
  assert.equal(isAllBoardAssigneesSelection(['3', '4'], HR, { currentUserId: ME }), true);
  assert.equal(isAllBoardAssigneesSelection(['4', '3', String(ME)], HR, { currentUserId: ME }), true);
  assert.equal(isAllBoardAssigneesSelection(['3'], HR, { currentUserId: ME }), false);
  assert.equal(isAllBoardAssigneesSelection(['3', '4', '9'], HR, { currentUserId: ME }), false);
  // У доски с одним человеком выбора «Все» нет вовсе.
  assert.equal(isAllBoardAssigneesSelection(['5'], ACC, { currentUserId: ME }), false);
  assert.equal(isAllBoardAssigneesSelection(['7'], FIN, { currentUserId: ME }), false);
  // Без отметок «Все» — весь состав доски.
  const team = { id: 60, members: [{ id: 21, name: 'А' }, { id: 22, name: 'Б' }, { id: ME, name: 'Я' }] };
  assert.equal(isAllBoardAssigneesSelection(['21', '22'], team, { currentUserId: ME }), true);
});

test('«Все» переезжает на новую доску всеми её исполнителями, «Себе» — если вы исполнитель и там', () => {
  const other = {
    id: 40,
    members: [
      { id: 11, name: 'Б', is_executor: true },
      { id: 12, name: 'В', is_executor: true },
      { id: ME, name: 'А', is_executor: true },
    ],
  };
  assert.deepEqual(
    assigneesAfterProjectChange({ assignAll: true, assigneeIds: ['3', '4', String(ME)] }, other, { currentUserId: ME }),
    { assignAll: true, assigneeIds: ['11', '12', String(ME)] },
  );
  assert.deepEqual(
    assigneesAfterProjectChange({ assignAll: true, assigneeIds: ['3', '4'] }, other, { currentUserId: ME }),
    { assignAll: true, assigneeIds: ['11', '12'] },
  );
});

test('доска без «Все» возвращает к выбору людей: «Все» чужой доски не переносится', () => {
  for (const board of [FIN, ACC, EMPTY, null]) {
    assert.deepEqual(
      assigneesAfterProjectChange({ assignAll: true, assigneeIds: ['3', '4'] }, board, { currentUserId: ME }),
      { assignAll: false, assigneeIds: [] },
    );
  }
});

test('поштучный выбор: на доске — только её люди, у доски без людей — кому вы вправе поручать', () => {
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '7', '9'] }, HR, { currentUserId: ME, recipientIds: new Set(['7']) }),
    { assignAll: false, assigneeIds: ['3'] },
  );
  // Доска без отметок: остаётся тот, кто в её составе, — получатели тут ни при чём.
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '7'] }, FIN, { currentUserId: ME, recipientIds: new Set(['3']) }),
    { assignAll: false, assigneeIds: ['7'] },
  );
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '7'] }, EMPTY, { currentUserId: ME, recipientIds: new Set(['7']) }),
    { assignAll: false, assigneeIds: ['7'] },
  );
  // Без списка получателей (ещё грузится) — ничего не выкидываем.
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '7'] }, EMPTY, { currentUserId: ME }),
    { assignAll: false, assigneeIds: ['3', '7'] },
  );
});
