/*
 * Исполнители доски в форме задачи (владелец 24.11.2026): «по каждой доске своя
 * привязка — можно выбрать только тех, кто есть в проекте как исполнитель», и
 * «выбрать всех либо конкретных людей». Сервер держит то же правило
 * (tests/test_task_project_executors.py).
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  allExecutorIds,
  assigneesAfterProjectChange,
  hasExecutorBinding,
  isAllExecutorsSelection,
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
// Доска без привязки: исполнители не отмечены — поручают как раньше.
const FIN = { id: 20, code: 'FIN', members: [{ id: 7, name: 'Финов Фёдор', access_level: 'member' }] };
// Один исполнитель — выбирать «всех» не из кого.
const ACC = { id: 30, code: 'ACC', members: [{ id: 5, name: 'Бухова Бэла', access_level: 'member', is_executor: true }] };

test('исполнители доски — отмеченные в составе, по имени; без отметок привязки нет', () => {
  // По имени: Аралова, Постановщик, Ясенева.
  assert.deepEqual(projectExecutors(HR).map((person) => person.id), [3, ME, 4]);
  assert.equal(hasExecutorBinding(HR), true);
  assert.equal(hasExecutorBinding(FIN), false);
  assert.equal(hasExecutorBinding(null), false);
  assert.deepEqual(projectExecutors(undefined), []);
});

test('«Все» — каждый исполнитель доски, кроме постановщика; у продакшна — только разрешённые', () => {
  assert.deepEqual(allExecutorIds(HR, { currentUserId: ME }), ['3', '4']);
  assert.deepEqual(allExecutorIds(HR, { currentUserId: 99 }), ['3', String(ME), '4']);
  assert.deepEqual(allExecutorIds(HR, { currentUserId: ME, allowedIds: new Set(['4']) }), ['4']);
  assert.deepEqual(allExecutorIds(FIN, { currentUserId: ME }), []);
});

test('при смене доски остаются только исполнители новой доски, у доски без привязки — все', () => {
  assert.deepEqual(keepAssigneesForProject(['3', '7', '9'], HR), ['3']);
  assert.deepEqual(keepAssigneesForProject(['3', '7'], FIN), ['3', '7']);
  assert.deepEqual(keepAssigneesForProject(undefined, HR), []);
});

test('правка открывается на «Все», только если в задаче ровно все исполнители (себя не считаем)', () => {
  assert.equal(isAllExecutorsSelection(['3', '4'], HR, { currentUserId: ME }), true);
  assert.equal(isAllExecutorsSelection(['4', '3', String(ME)], HR, { currentUserId: ME }), true);
  assert.equal(isAllExecutorsSelection(['3'], HR, { currentUserId: ME }), false);
  assert.equal(isAllExecutorsSelection(['3', '4', '9'], HR, { currentUserId: ME }), false);
  // У доски с одним исполнителем выбора «Все» нет вовсе.
  assert.equal(isAllExecutorsSelection(['5'], ACC, { currentUserId: ME }), false);
  assert.equal(isAllExecutorsSelection(['7'], FIN, { currentUserId: ME }), false);
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
  for (const board of [FIN, ACC, null]) {
    assert.deepEqual(
      assigneesAfterProjectChange({ assignAll: true, assigneeIds: ['3', '4'] }, board, { currentUserId: ME }),
      { assignAll: false, assigneeIds: [] },
    );
  }
});

test('поштучный выбор: на доске с привязкой — её исполнители, без привязки — кому вы вправе поручать', () => {
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '7', '9'] }, HR, { currentUserId: ME, recipientIds: new Set(['7']) }),
    { assignAll: false, assigneeIds: ['3'] },
  );
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '7'] }, FIN, { currentUserId: ME, recipientIds: new Set(['7']) }),
    { assignAll: false, assigneeIds: ['7'] },
  );
  // Без списка получателей (ещё грузится) — ничего не выкидываем.
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '7'] }, FIN, { currentUserId: ME }),
    { assignAll: false, assigneeIds: ['3', '7'] },
  );
});
