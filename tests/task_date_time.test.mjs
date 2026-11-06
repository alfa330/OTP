import test from 'node:test';
import assert from 'node:assert/strict';

import { DEFAULT_DUE_TIME, joinLocalDateTime, splitLocalDateTime } from '../src/components/tasks/taskDateTime.js';

test('строка системного поля делится на день и время', () => {
  assert.deepEqual(splitLocalDateTime('2026-11-05T14:30'), { date: '2026-11-05', time: '14:30' });
  // Хвост с секундами системное поле тоже умело отдавать — время берём до минут.
  assert.deepEqual(splitLocalDateTime('2026-11-05T14:30:00'), { date: '2026-11-05', time: '14:30' });
  assert.deepEqual(splitLocalDateTime('2026-11-05'), { date: '2026-11-05', time: '' });
});

test('пустое и чужое — без срока', () => {
  [null, undefined, '', 'завтра', '05.11.2026 14:30'].forEach((value) => {
    assert.deepEqual(splitLocalDateTime(value), { date: '', time: '' }, String(value));
  });
});

test('обратно — та же строка, что отдавало системное поле', () => {
  assert.equal(joinLocalDateTime('2026-11-05', '09:15'), '2026-11-05T09:15');
  const { date, time } = splitLocalDateTime('2026-12-31T23:55');
  assert.equal(joinLocalDateTime(date, time), '2026-12-31T23:55');
});

test('день без времени встаёт на конец рабочего дня, без дня срока нет вовсе', () => {
  assert.equal(DEFAULT_DUE_TIME, '18:00');
  assert.equal(joinLocalDateTime('2026-11-05', ''), '2026-11-05T18:00');
  assert.equal(joinLocalDateTime('', '10:00'), '');
});
