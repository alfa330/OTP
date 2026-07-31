import test from 'node:test';
import assert from 'node:assert/strict';

import {
  collectTaskActionNeeds,
  reviewAuthorityId,
  taskActionNeed,
} from '../src/components/tasks/taskActionNeeds.js';

const ME = 7;
const NOW = Date.parse('2026-07-31T12:00:00');
const PAST = '2026-07-30T18:00:00';
const FUTURE = '2026-08-05T18:00:00';

const task = (fields) => ({
  id: 1,
  status: 'assigned',
  is_backlog: false,
  assignee: { id: ME, name: 'Я' },
  creator: { id: 42, name: 'Руководитель' },
  ...fields,
});

test('назначенная задача ждёт исполнителя, пока он её не начал', () => {
  assert.equal(taskActionNeed(task({}), ME, NOW)?.kind, 'fresh');
  assert.equal(taskActionNeed(task({ status: 'in_progress' }), ME, NOW), null);
});

test('просрочка перебивает остальные причины и работает для начатых задач', () => {
  assert.equal(taskActionNeed(task({ due_at: PAST }), ME, NOW)?.kind, 'overdue');
  assert.equal(taskActionNeed(task({ status: 'in_progress', due_at: PAST }), ME, NOW)?.kind, 'overdue');
  assert.equal(taskActionNeed(task({ status: 'returned', due_at: PAST }), ME, NOW)?.kind, 'overdue');
  assert.equal(taskActionNeed(task({ due_at: FUTURE }), ME, NOW)?.kind, 'fresh');
});

test('возврат на доработку — причина для исполнителя', () => {
  assert.equal(taskActionNeed(task({ status: 'returned' }), ME, NOW)?.kind, 'returned');
});

test('бэклог и закрытые задачи никого не ждут', () => {
  assert.equal(taskActionNeed(task({ is_backlog: true, due_at: PAST }), ME, NOW), null);
  assert.equal(taskActionNeed(task({ status: 'accepted', due_at: PAST }), ME, NOW), null);
});

test('сданную работу ждёт поручитель, а не исполнитель', () => {
  const submitted = task({ id: 2, status: 'completed', assignee: { id: 99, name: 'Коллега' } });
  assert.equal(taskActionNeed(submitted, 42, NOW)?.kind, 'review');
  assert.equal(taskActionNeed(submitted, 99, NOW), null);
});

test('поручитель важнее постановщика: приёмка уходит к requested_by', () => {
  const submitted = task({
    id: 3,
    status: 'completed',
    assignee: { id: 99, name: 'Коллега' },
    creator: { id: 42, name: 'Секретарь' },
    requested_by: { id: 55, name: 'Директор', source: 'user' },
  });
  assert.equal(reviewAuthorityId(submitted), 55);
  assert.equal(taskActionNeed(submitted, 55, NOW)?.kind, 'review');
  assert.equal(taskActionNeed(submitted, 42, NOW), null);
});

test('внешний источник без id не отбирает приёмку у постановщика', () => {
  const submitted = task({
    id: 4,
    status: 'completed',
    assignee: { id: 99, name: 'Коллега' },
    requested_by: { id: null, name: 'Клиент', source: 'external' },
  });
  assert.equal(reviewAuthorityId(submitted), 42);
  assert.equal(taskActionNeed(submitted, 42, NOW)?.kind, 'review');
});

test('свою же задачу принимает автор — иначе принимать некому', () => {
  const selfTask = task({ id: 5, status: 'completed', assignee: { id: ME, name: 'Я' }, creator: { id: ME, name: 'Я' } });
  assert.equal(taskActionNeed(selfTask, ME, NOW)?.kind, 'review');
});

test('список ждущих задач: сначала срочные, внутри группы — по дедлайну', () => {
  const needs = collectTaskActionNeeds([
    task({ id: 10 }),
    task({ id: 11, status: 'returned' }),
    task({ id: 12, status: 'completed', assignee: { id: 99, name: 'Коллега' }, creator: { id: ME, name: 'Я' } }),
    task({ id: 13, due_at: PAST }),
    task({ id: 14, due_at: '2026-07-29T09:00:00' }),
    task({ id: 15, status: 'accepted' }),
    task({ id: 16, assignee: { id: 99, name: 'Коллега' } }),
  ], ME, NOW);

  assert.deepEqual(needs.map((need) => need.task.id), [14, 13, 11, 12, 10]);
  assert.deepEqual(needs.map((need) => need.kind), ['overdue', 'overdue', 'returned', 'review', 'fresh']);
});

test('задача считается один раз — счётчик бейджа равен длине списка', () => {
  const needs = collectTaskActionNeeds([task({ id: 20, status: 'returned', due_at: PAST })], ME, NOW);
  assert.equal(needs.length, 1);
  assert.equal(needs[0].kind, 'overdue');
});
