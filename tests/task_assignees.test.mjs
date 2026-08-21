import test from 'node:test';
import assert from 'node:assert/strict';

import {
  TASK_MAX_ASSIGNEES,
  isTaskAssignee,
  taskAssigneeIds,
  taskAssignees,
  taskAssigneesLabel,
} from '../src/components/tasks/taskAssignees.js';
import { taskActionNeed } from '../src/components/tasks/taskActionNeeds.js';

const AIGUL = { id: 11, name: 'Айгуль' };
const SERGEY = { id: 12, name: 'Сергей' };
const DINA = { id: 13, name: 'Дина' };
const BOSS = { id: 42, name: 'Руководитель' };

const NOW = Date.parse('2026-07-31T12:00:00');
const PAST = '2026-07-30T18:00:00';

const task = (fields) => ({
  id: 1,
  status: 'assigned',
  is_backlog: false,
  updated_at: '2026-07-31T09:00:00',
  creator: BOSS,
  ...fields,
});

test('состав читается из assignees и сохраняет порядок', () => {
  const list = taskAssignees(task({ assignee: AIGUL, assignees: [AIGUL, SERGEY, DINA] }));
  assert.deepEqual(list.map((person) => person.id), [11, 12, 13]);
  assert.deepEqual(taskAssigneeIds(task({ assignees: [SERGEY, AIGUL] })), ['12', '11']);
});

/* Главная защита совместимости: сервер, который ещё не отдаёт assignees (или
   ответ из кеша), должен читаться как задача с одним исполнителем, а не как
   задача без исполнителя вообще. */
test('ответ без assignees читается как один исполнитель', () => {
  const legacy = task({ assignee: AIGUL });
  assert.deepEqual(taskAssignees(legacy), [AIGUL]);
  assert.ok(isTaskAssignee(legacy, 11));
  assert.equal(isTaskAssignee(legacy, 12), false);
});

test('пустой и мусорный состав не притворяются исполнителями', () => {
  assert.deepEqual(taskAssignees(task({})), []);
  assert.deepEqual(taskAssignees(task({ assignees: [] })), []);
  assert.deepEqual(taskAssignees(task({ assignees: [{ id: 0 }, null] })), []);
  assert.equal(isTaskAssignee(task({ assignees: [AIGUL] }), 0), false);
  assert.equal(isTaskAssignee(null, 11), false);
});

/* assignees главнее assignee: если сервер прислал оба, состав берём из массива,
   иначе снятый исполнитель остался бы «в задаче» по старому полю. */
test('assignees главнее одиночного assignee', () => {
  const moved = task({ assignee: AIGUL, assignees: [SERGEY] });
  assert.deepEqual(taskAssignees(moved), [SERGEY]);
  assert.equal(isTaskAssignee(moved, 11), false);
  assert.ok(isTaskAssignee(moved, 12));
});

test('id сравниваются числами, даже если пришли строками', () => {
  const stringy = task({ assignees: [{ id: '12', name: 'Сергей' }] });
  assert.ok(isTaskAssignee(stringy, 12));
  assert.ok(isTaskAssignee(stringy, '12'));
});

test('подпись состава: имя одного, «и ещё N» для остальных', () => {
  assert.equal(taskAssigneesLabel(task({ assignees: [AIGUL] })), 'Айгуль');
  assert.equal(taskAssigneesLabel(task({ assignees: [AIGUL, SERGEY] })), 'Айгуль и ещё 1');
  assert.equal(taskAssigneesLabel(task({ assignees: [AIGUL, SERGEY, DINA] })), 'Айгуль и ещё 2');
  assert.equal(taskAssigneesLabel(task({})), '—');
  assert.equal(taskAssigneesLabel(task({}), 'нет'), 'нет');
});

test('потолок состава задан и не бесконечный', () => {
  assert.ok(TASK_MAX_ASSIGNEES >= 2 && TASK_MAX_ASSIGNEES <= 25);
});

/* Смысл всей затеи: задача, поручённая нескольким, ждёт КАЖДОГО из них —
   а не только того, кто в tasks.assigned_to. */
test('задача ждёт каждого исполнителя, а не только первого', () => {
  const shared = task({ assignee: AIGUL, assignees: [AIGUL, SERGEY] });
  assert.equal(taskActionNeed(shared, 11, NOW)?.kind, 'fresh');
  assert.equal(taskActionNeed(shared, 12, NOW)?.kind, 'fresh');
  assert.equal(taskActionNeed(shared, 99, NOW), null);
});

test('просрочка и возврат касаются каждого исполнителя', () => {
  const overdue = task({ assignees: [AIGUL, SERGEY], due_at: PAST });
  assert.equal(taskActionNeed(overdue, 12, NOW)?.kind, 'overdue');
  const returned = task({ assignees: [AIGUL, SERGEY], status: 'returned' });
  assert.equal(taskActionNeed(returned, 12, NOW)?.kind, 'returned');
});

test('приёмку сообщаем всем исполнителям, кроме того, кто сам принял', () => {
  const accepted = task({ status: 'accepted', assignees: [AIGUL, SERGEY] });
  assert.equal(taskActionNeed(accepted, 11, NOW)?.kind, 'accepted');
  assert.equal(taskActionNeed(accepted, 12, NOW)?.kind, 'accepted');
  // Постановщик он же исполнитель — о своём же клике не сообщаем.
  const selfAccepted = task({ status: 'accepted', creator: AIGUL, assignees: [AIGUL, SERGEY] });
  assert.equal(taskActionNeed(selfAccepted, 11, NOW), null);
  assert.equal(taskActionNeed(selfAccepted, 12, NOW)?.kind, 'accepted');
});

/* «Просят информацию» адресовано стороне постановки. Соисполнитель — не
   постановщик, и запрос коллеги не должен превращаться в его задачу. */
test('запрос информации не прилетает соисполнителю', () => {
  const asking = task({
    assignees: [AIGUL, SERGEY],
    info_request: { id: 5, body: 'Нужен доступ' },
  });
  assert.equal(taskActionNeed(asking, 42, NOW)?.kind, 'info');
  assert.equal(taskActionNeed(asking, 12, NOW)?.kind, 'fresh');
});
