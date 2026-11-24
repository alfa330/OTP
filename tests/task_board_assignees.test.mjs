/*
 * Люди доски в форме задачи (владелец 24.11.2026): «по каждой доске своя привязка»,
 * «выбрать всех либо конкретных людей» и «если человека нет в данном проекте, зачем
 * выводить его в исполнителях». Люди доски — весь её состав; отдельную отметку
 * исполнителей владелец убрал. Доска без людей — как раньше.
 * Сервер держит то же правило (tests/test_task_board_assignees.py).
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
} from '../src/components/tasks/taskProjects.js';

const ME = 1;
// Люди выдуманы. HR: участники и «доска», постановщик тоже в составе.
const HR = {
  id: 10,
  code: 'HR',
  members: [
    { id: 4, name: 'Ясенева Яна', access_level: 'member' },
    { id: 3, name: 'Аралова Ая', access_level: 'board' },
    { id: ME, name: 'Постановщик Пётр', access_level: 'member' },
    { id: 9, name: 'Наблюдатель Нина', access_level: 'board' },
  ],
};
// Один человек — выбирать «всех» не из кого.
const FIN = { id: 20, code: 'FIN', members: [{ id: 7, name: 'Финов Фёдор', access_level: 'member' }] };
// Доска без людей вовсе — поручают как раньше.
const EMPTY = { id: 40, code: 'BE', members: [] };

test('люди доски — весь её состав по имени, любого уровня; без людей — как раньше', () => {
  // По имени: Аралова, Наблюдатель, Постановщик, Ясенева.
  assert.deepEqual(boardAssignees(HR).map((person) => person.id), [3, 9, ME, 4]);
  assert.deepEqual(boardAssignees(FIN).map((person) => person.id), [7]);
  assert.equal(hasBoardAssignees(HR), true);
  assert.equal(hasBoardAssignees(EMPTY), false);
  assert.equal(hasBoardAssignees(null), false);
  assert.deepEqual(boardAssignees(undefined), []);
});

test('«Все» — каждый из состава доски, кроме постановщика; у продакшна — только разрешённые', () => {
  assert.deepEqual(allBoardAssigneeIds(HR, { currentUserId: ME }), ['3', '9', '4']);
  assert.deepEqual(allBoardAssigneeIds(HR, { currentUserId: 99 }), ['3', '9', String(ME), '4']);
  assert.deepEqual(allBoardAssigneeIds(HR, { currentUserId: ME, allowedIds: new Set(['4']) }), ['4']);
  assert.deepEqual(allBoardAssigneeIds(FIN, { currentUserId: ME }), ['7']);
  assert.deepEqual(allBoardAssigneeIds(EMPTY, { currentUserId: ME }), []);
});

test('при смене доски остаются только люди новой доски, у доски без людей — все', () => {
  assert.deepEqual(keepAssigneesForProject(['3', '7', '9'], HR), ['3', '9']);
  assert.deepEqual(keepAssigneesForProject(['3', '7'], FIN), ['7']);
  assert.deepEqual(keepAssigneesForProject(['3', '7'], EMPTY), ['3', '7']);
  assert.deepEqual(keepAssigneesForProject(undefined, HR), []);
});

test('правка открывается на «Все», только если в задаче ровно все люди доски (себя не считаем)', () => {
  assert.equal(isAllBoardAssigneesSelection(['3', '4', '9'], HR, { currentUserId: ME }), true);
  assert.equal(isAllBoardAssigneesSelection(['4', '9', '3', String(ME)], HR, { currentUserId: ME }), true);
  assert.equal(isAllBoardAssigneesSelection(['3', '4'], HR, { currentUserId: ME }), false);
  assert.equal(isAllBoardAssigneesSelection(['3', '4', '9', '50'], HR, { currentUserId: ME }), false);
  // У доски с одним человеком выбора «Все» нет вовсе.
  assert.equal(isAllBoardAssigneesSelection(['7'], FIN, { currentUserId: ME }), false);
});

test('«Все» переезжает на новую доску всем её составом, «Себе» — если вы есть и там', () => {
  const other = {
    id: 60,
    members: [{ id: 11, name: 'Б' }, { id: 12, name: 'В' }, { id: ME, name: 'А' }],
  };
  assert.deepEqual(
    assigneesAfterProjectChange({ assignAll: true, assigneeIds: ['3', '4', String(ME)] }, other, { currentUserId: ME }),
    { assignAll: true, assigneeIds: ['11', '12', String(ME)] },
  );
  assert.deepEqual(
    assigneesAfterProjectChange({ assignAll: true, assigneeIds: ['3', '4'] }, other, { currentUserId: ME }),
    { assignAll: true, assigneeIds: ['11', '12'] },
  );
  // Вас в составе новой доски нет — «Себе» не переносится.
  const withoutMe = { id: 61, members: [{ id: 11, name: 'Б' }, { id: 12, name: 'В' }] };
  assert.deepEqual(
    assigneesAfterProjectChange({ assignAll: true, assigneeIds: ['3', '4', String(ME)] }, withoutMe, { currentUserId: ME }),
    { assignAll: true, assigneeIds: ['11', '12'] },
  );
});

test('доска без «Все» возвращает к выбору людей: «Все» чужой доски не переносится', () => {
  for (const board of [FIN, EMPTY, null]) {
    assert.deepEqual(
      assigneesAfterProjectChange({ assignAll: true, assigneeIds: ['3', '4'] }, board, { currentUserId: ME }),
      { assignAll: false, assigneeIds: [] },
    );
  }
});

test('поштучный выбор: на доске — только её люди, у доски без людей — кому вы вправе поручать', () => {
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '7', '50'] }, HR, { currentUserId: ME, recipientIds: new Set(['7']) }),
    { assignAll: false, assigneeIds: ['3'] },
  );
  // Получатели тут ни при чём: остаётся тот, кто в составе доски.
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
