import test from 'node:test';
import assert from 'node:assert/strict';

import { dayKeyOf, dayLabelOf, groupTasksByDay } from '../src/components/tasks/boardGrouping.js';

const NOW = Date.parse('2026-07-31T12:00:00');

const task = (id, created) => ({ id, created_at: created });

test('день считается по локальной дате, а не по UTC', () => {
  // Вечерняя задача не должна уехать в следующий день.
  assert.equal(dayKeyOf('2026-07-31T23:40:00'), '2026-07-31');
  assert.equal(dayKeyOf('2026-07-31T00:10:00'), '2026-07-31');
  assert.equal(dayKeyOf(''), '');
  assert.equal(dayKeyOf('не дата'), '');
});

test('подписи дней: сегодня, вчера, завтра, дата', () => {
  assert.equal(dayLabelOf('2026-07-31T09:00:00', NOW), 'Сегодня');
  assert.equal(dayLabelOf('2026-07-30T09:00:00', NOW), 'Вчера');
  assert.equal(dayLabelOf('2026-08-01T09:00:00', NOW), 'Завтра');
  assert.equal(dayLabelOf('2026-07-12T09:00:00', NOW), '12 июля');
  // Прошлый год подписывается с годом, иначе «12 июля» двусмысленно.
  assert.match(dayLabelOf('2025-07-12T09:00:00', NOW), /2025/);
  assert.equal(dayLabelOf(null, NOW), 'Без даты');
});

test('группы идут в том порядке, в каком задачи прислал сервер', () => {
  const groups = groupTasksByDay([
    task(1, '2026-07-31T18:00:00'),
    task(2, '2026-07-31T09:00:00'),
    task(3, '2026-07-29T10:00:00'),
    task(4, '2026-07-30T10:00:00'),
  ], { now: NOW });

  assert.deepEqual(groups.map((group) => group.label), ['Сегодня', '29 июля', 'Вчера']);
  assert.deepEqual(groups[0].tasks.map((item) => item.id), [1, 2]);
  assert.deepEqual(groups[1].tasks.map((item) => item.id), [3]);
});

test('задачи одного дня собираются в одну группу, даже если пришли не подряд', () => {
  // При сортировке по важности день может встретиться снова — второй заголовок не нужен.
  const groups = groupTasksByDay([
    task(1, '2026-07-31T18:00:00'),
    task(2, '2026-07-29T10:00:00'),
    task(3, '2026-07-31T08:00:00'),
  ], { now: NOW });

  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0].tasks.map((item) => item.id), [1, 3]);
});

test('задачи без даты не теряются', () => {
  const groups = groupTasksByDay([task(1, null), task(2, '2026-07-31T10:00:00')], { now: NOW });
  const unknown = groups.find((group) => group.label === 'Без даты');
  assert.ok(unknown);
  assert.deepEqual(unknown.tasks.map((item) => item.id), [1]);
});

test('можно группировать по другому полю — например по дедлайну', () => {
  const groups = groupTasksByDay(
    [{ id: 1, created_at: '2026-07-20T10:00:00', due_at: '2026-07-31T10:00:00' }],
    { field: 'due_at', now: NOW }
  );
  assert.equal(groups[0].label, 'Сегодня');
});

test('пустой и некорректный вход не ломают раскладку', () => {
  assert.deepEqual(groupTasksByDay([]), []);
  assert.deepEqual(groupTasksByDay(null), []);
});
