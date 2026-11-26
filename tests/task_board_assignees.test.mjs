/*
 * Исполнители и участники доски в форме задачи (владелец 26.11.2026): «у каждой
 * доски свои исполнители, их можно выбирать, а остальные как участники». В форме
 * задачи доски — только её исполнители, всех разом («Все») или поштучно; участников
 * там нет. Доска без людей — как раньше; доска с людьми без исполнителей — пусто.
 * Сервер держит то же правило (tests/test_task_board_assignees.py).
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  allBoardAssigneeIds,
  assigneesAfterProjectChange,
  boardAssignees,
  boardMembersIssue,
  boardRoleCounts,
  boardWithoutExecutors,
  hasBoardAssignees,
  hasBoardPeople,
  isAllBoardAssigneesSelection,
  keepAssigneesForProject,
  orderBoardMembers,
  projectListFacts,
  watcherCandidates,
} from '../src/components/tasks/taskProjects.js';

const ME = 1;
// Люди выдуманы. HR: три исполнителя (один видит только свои), постановщик тоже
// исполнитель, плюс два участника — руководитель и СВ, который ставит заявки.
const HR = {
  id: 10,
  code: 'HR',
  members: [
    { id: 4, name: 'Ясенева Яна', access_level: 'member', is_executor: true },
    { id: 3, name: 'Аралова Ая', access_level: 'board', is_executor: true },
    { id: ME, name: 'Постановщик Пётр', access_level: 'member', is_executor: true },
    { id: 9, name: 'Наблюдатель Нина', access_level: 'board', is_executor: false },
    { id: 8, name: 'Глава Галина', access_level: 'member', is_executor: false },
  ],
};
// Один исполнитель и участник — выбирать «всех» не из кого.
const FIN = {
  id: 20,
  code: 'FIN',
  members: [
    { id: 7, name: 'Финов Фёдор', access_level: 'member', is_executor: true },
    { id: 8, name: 'Глава Галина', access_level: 'member', is_executor: false },
  ],
};
// Люди есть, исполнителей нет — поручать некому.
const WATCH_ONLY = {
  id: 30,
  code: 'MKT',
  members: [{ id: 8, name: 'Глава Галина', access_level: 'member', is_executor: false }],
};
// Доска без людей вовсе — поручают как раньше.
const EMPTY = { id: 40, code: 'BE', members: [] };

test('в форме — только исполнители доски по имени; участники и уровень видимости роли не меняют', () => {
  // По имени: Аралова, Постановщик, Ясенева. Нина и Галина — участники.
  assert.deepEqual(boardAssignees(HR).map((person) => person.id), [3, ME, 4]);
  assert.deepEqual(boardAssignees(FIN).map((person) => person.id), [7]);
  assert.deepEqual(boardAssignees(WATCH_ONLY), []);
  assert.deepEqual(boardAssignees(EMPTY), []);
  assert.deepEqual(boardAssignees(undefined), []);
});

test('старый сервер роли не присылает — исполнители весь состав, как было у него', () => {
  // Сайт выезжает раньше сервера: без этого все доски на время выкладки
  // показали бы «нет исполнителей».
  const old = { id: 70, members: [{ id: 6, name: 'В' }, { id: 5, name: 'Б' }] };
  assert.deepEqual(boardAssignees(old).map((person) => person.id), [5, 6]);
  assert.equal(boardWithoutExecutors(old), false);
  assert.deepEqual(allBoardAssigneeIds(old, { currentUserId: ME }), ['5', '6']);
  assert.deepEqual(boardRoleCounts(old.members), { executors: 2, participants: 0 });
  assert.equal(boardMembersIssue(old.members), '');
  assert.deepEqual(projectListFacts(old).facts, ['Без отдела', '2 исполнителя']);
  // Как только роль пришла хоть у одного — считаем по ролям, недостающая = участник.
  const mixed = { members: [{ id: 5, name: 'Б', is_executor: true }, { id: 6, name: 'В' }] };
  assert.deepEqual(boardAssignees(mixed).map((person) => person.id), [5]);
  // Сами строки состава не меняются.
  assert.equal('is_executor' in old.members[0], false);
});

test('привязка есть у доски с людьми, даже без исполнителей; без людей — как раньше', () => {
  assert.equal(hasBoardPeople(HR), true);
  assert.equal(hasBoardPeople(WATCH_ONLY), true);
  assert.equal(hasBoardPeople(EMPTY), false);
  assert.equal(hasBoardPeople(null), false);
  assert.equal(hasBoardAssignees(HR), true);
  assert.equal(hasBoardAssignees(WATCH_ONLY), false);
  assert.equal(hasBoardAssignees(EMPTY), false);
});

test('«Все» — каждый исполнитель доски, кроме постановщика; у продакшна — только разрешённые', () => {
  assert.deepEqual(allBoardAssigneeIds(HR, { currentUserId: ME }), ['3', '4']);
  assert.deepEqual(allBoardAssigneeIds(HR, { currentUserId: 99 }), ['3', String(ME), '4']);
  assert.deepEqual(allBoardAssigneeIds(HR, { currentUserId: ME, allowedIds: new Set(['4']) }), ['4']);
  assert.deepEqual(allBoardAssigneeIds(FIN, { currentUserId: ME }), ['7']);
  assert.deepEqual(allBoardAssigneeIds(WATCH_ONLY, { currentUserId: ME }), []);
  assert.deepEqual(allBoardAssigneeIds(EMPTY, { currentUserId: ME }), []);
});

test('при смене доски остаются только её исполнители; у доски без людей — все', () => {
  // Нина (9) — на HR участник: из исполнителей уходит.
  assert.deepEqual(keepAssigneesForProject(['3', '7', '9'], HR), ['3']);
  assert.deepEqual(keepAssigneesForProject(['3', '7', '8'], FIN), ['7']);
  // Люди есть, исполнителей нет — не остаётся никого, «как раньше» тут не действует.
  assert.deepEqual(keepAssigneesForProject(['3', '8'], WATCH_ONLY), []);
  assert.deepEqual(keepAssigneesForProject(['3', '7'], EMPTY), ['3', '7']);
  assert.deepEqual(keepAssigneesForProject(undefined, HR), []);
});

test('правка открывается на «Все», только если в задаче ровно все исполнители доски (себя не считаем)', () => {
  assert.equal(isAllBoardAssigneesSelection(['3', '4'], HR, { currentUserId: ME }), true);
  assert.equal(isAllBoardAssigneesSelection(['4', '3', String(ME)], HR, { currentUserId: ME }), true);
  assert.equal(isAllBoardAssigneesSelection(['3'], HR, { currentUserId: ME }), false);
  // Участник сверх исполнителей — это уже «конкретные люди».
  assert.equal(isAllBoardAssigneesSelection(['3', '4', '9'], HR, { currentUserId: ME }), false);
  // У доски с одним исполнителем выбора «Все» нет вовсе.
  assert.equal(isAllBoardAssigneesSelection(['7'], FIN, { currentUserId: ME }), false);
});

test('«Все» переезжает на новую доску всеми её исполнителями, «Себе» — если вы исполнитель и там', () => {
  const other = {
    id: 60,
    members: [
      { id: 11, name: 'Б', is_executor: true },
      { id: 12, name: 'В', is_executor: true },
      { id: ME, name: 'А', is_executor: true },
      { id: 13, name: 'Г', is_executor: false },
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
  // На новой доске вы участник — «Себе» не переносится.
  const meParticipant = {
    id: 61,
    members: [
      { id: 11, name: 'Б', is_executor: true },
      { id: 12, name: 'В', is_executor: true },
      { id: ME, name: 'А', is_executor: false },
    ],
  };
  assert.deepEqual(
    assigneesAfterProjectChange({ assignAll: true, assigneeIds: ['3', '4', String(ME)] }, meParticipant, { currentUserId: ME }),
    { assignAll: true, assigneeIds: ['11', '12'] },
  );
});

test('доска без «Все» возвращает к выбору людей: «Все» чужой доски не переносится', () => {
  for (const board of [FIN, WATCH_ONLY, EMPTY, null]) {
    assert.deepEqual(
      assigneesAfterProjectChange({ assignAll: true, assigneeIds: ['3', '4'] }, board, { currentUserId: ME }),
      { assignAll: false, assigneeIds: [] },
    );
  }
});

test('поштучный выбор: на доске — только её исполнители, у доски без людей — кому вы вправе поручать', () => {
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '7', '9', '50'] }, HR, { currentUserId: ME, recipientIds: new Set(['7', '9']) }),
    { assignAll: false, assigneeIds: ['3'] },
  );
  // Получатели тут ни при чём: остаётся тот, кто исполнитель доски.
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '7'] }, FIN, { currentUserId: ME, recipientIds: new Set(['3']) }),
    { assignAll: false, assigneeIds: ['7'] },
  );
  // Люди есть, исполнителей нет — получатели доску без исполнителей не обходят.
  assert.deepEqual(
    assigneesAfterProjectChange({ assigneeIds: ['3', '8'] }, WATCH_ONLY, { currentUserId: ME, recipientIds: new Set(['3', '8']) }),
    { assignAll: false, assigneeIds: [] },
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

test('счётчики ролей и отказ окна «Проекты»: люди есть — нужен хотя бы один исполнитель', () => {
  assert.deepEqual(boardRoleCounts(HR.members), { executors: 3, participants: 2 });
  assert.deepEqual(boardRoleCounts(WATCH_ONLY.members), { executors: 0, participants: 1 });
  assert.deepEqual(boardRoleCounts([]), { executors: 0, participants: 0 });
  assert.deepEqual(boardRoleCounts(undefined), { executors: 0, participants: 0 });
  assert.equal(boardMembersIssue(HR.members), '');
  assert.equal(boardMembersIssue([]), '');
  assert.match(boardMembersIssue(WATCH_ONLY.members), /хотя бы одного исполнителя/);
  // Архивной доске задачи не ставят — исполнители ей не нужны.
  assert.equal(boardMembersIssue(WATCH_ONLY.members, true), '');
});

test('«у доски нет исполнителей» — только когда люди есть, а исполнителей нет', () => {
  assert.equal(boardWithoutExecutors(WATCH_ONLY), true);
  assert.equal(boardWithoutExecutors(HR), false);
  // Доска без людей — «как раньше», а не «некому».
  assert.equal(boardWithoutExecutors(EMPTY), false);
  assert.equal(boardWithoutExecutors(null), false);
});

test('в окне «Проекты» исполнители сверху, дальше участники, внутри по имени', () => {
  assert.deepEqual(orderBoardMembers(HR.members).map((person) => person.id), [3, ME, 4, 8, 9]);
  // Исходный список не трогаем: порядок черновика — копия.
  assert.deepEqual(HR.members.map((person) => person.id), [4, 3, ME, 9, 8]);
  assert.deepEqual(orderBoardMembers(undefined), []);
});

test('строка доски в списке: исполнители и участники словами, «нет исполнителей» — только у живой доски', () => {
  const hr = { ...HR, department_name: 'HR', task_count: 21, sections: [{ id: 1 }, { id: 2 }] };
  assert.deepEqual(projectListFacts(hr), {
    facts: ['HR', '3 исполнителя', '2 участника', '2 раздела', '21 задача'],
    missingExecutors: false,
  });
  assert.deepEqual(projectListFacts({ ...WATCH_ONLY, department_name: 'Маркетинг' }), {
    facts: ['Маркетинг', '1 участник'],
    missingExecutors: true,
  });
  assert.deepEqual(projectListFacts({ ...WATCH_ONLY, is_archived: true }).missingExecutors, false);
  assert.deepEqual(projectListFacts(EMPTY), { facts: ['Без отдела', 'без состава'], missingExecutors: false });
  assert.deepEqual(projectListFacts({ members: [{ id: 5, is_executor: true }], task_count: 5 }).facts,
    ['Без отдела', '1 исполнитель', '5 задач']);
});

test('наблюдатели — те, кто видит только свои задачи, и у исполнителя, и у участника', () => {
  assert.deepEqual(watcherCandidates(HR).map((person) => person.id), [3, 9]);
  assert.deepEqual(watcherCandidates(HR, ['3']).map((person) => person.id), [9]);
  assert.deepEqual(watcherCandidates(FIN), []);
});
