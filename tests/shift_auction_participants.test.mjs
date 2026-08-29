import test from 'node:test';
import assert from 'node:assert/strict';

import {
  filterOperationalShiftAuctionOperators,
  normalizeShiftAuctionOperators,
  shouldHydrateShiftAuctionDraft,
} from '../src/components/resources/shiftAuctionParticipants.js';

test('participant selector keeps temporary statuses only for exact Osnova direction', () => {
  const rows = normalizeShiftAuctionOperators([
    { id: 1, name: 'Working', role: 'operator', status: 'working', direction: 'Основа' },
    { id: 2, name: 'BS', role: 'operator', status: 'bs', direction: 'Основа' },
    { id: 3, name: 'Sick', role: 'operator', status: 'sick_leave', direction: ' Основа ' },
    { id: 4, name: 'Leave', role: 'operator', status: 'annual_leave', direction: 'ОСНОВА' },
    { id: 5, name: 'Fired', role: 'operator', status: 'fired', direction: 'Основа' },
    { id: 6, name: 'Dismissed', role: 'operator', status: 'dismissal', direction: 'Основа' },
    { id: 7, name: 'Sales', role: 'operator', status: 'working', direction: 'Основа ОП' },
    { id: 8, name: 'Chat', role: 'operator', status: 'working', direction: 'Чат менеджер' },
    { id: 9, name: 'Supervisor', role: 'sv', status: 'working', direction: 'Основа' },
  ]);

  assert.deepEqual(rows.map((row) => row.id), [2, 4, 3, 1]);
  assert.equal(rows.find((row) => row.id === 2)?.status, 'bs');
});

test('live employee status overrides an older participant snapshot', () => {
  const snapshotRows = [
    { id: 10, name: 'Operator', role: 'operator', status: 'working', direction: 'Основа' },
  ];
  const liveRows = [
    { id: 10, name: 'Operator', role: 'operator', status: 'bs', direction: 'Основа' },
  ];

  const selectorRows = normalizeShiftAuctionOperators(liveRows, snapshotRows);
  assert.equal(selectorRows.length, 1);
  assert.equal(selectorRows[0].status, 'bs');
  assert.deepEqual(filterOperationalShiftAuctionOperators(selectorRows), []);
});

test('temporary status wins regardless of which independently refreshed source has it', () => {
  const snapshotRows = [
    { id: 11, name: 'Operator', role: 'operator', status: 'bs', direction: 'Основа' },
  ];
  const staleLiveRows = [
    { id: 11, name: 'Operator', role: 'operator', status: 'working', direction: 'Основа' },
  ];

  const selectorRows = normalizeShiftAuctionOperators(staleLiveRows, snapshotRows);
  assert.equal(selectorRows.length, 1);
  assert.equal(selectorRows[0].status, 'bs');
  assert.deepEqual(filterOperationalShiftAuctionOperators(selectorRows), []);
});

test('conflicting direction records fail closed', () => {
  const snapshotRows = [
    { id: 12, name: 'Operator', role: 'operator', status: 'working', direction: 'Основа' },
  ];
  const liveRows = [
    { id: 12, name: 'Operator', role: 'operator', status: 'working', direction: 'Основа ОП' },
  ];

  assert.deepEqual(normalizeShiftAuctionOperators(liveRows, snapshotRows), []);
});

test('operational rows contain only working Osnova operators', () => {
  const rows = [
    { id: 1, status: 'working', direction: 'Основа' },
    { id: 2, status: 'bs', direction: 'Основа' },
    { id: 3, status: 'sick_leave', direction: 'Основа' },
    { id: 4, status: 'working', direction: 'Основа ОП' },
    { id: 5, status: '', direction: 'Основа' },
  ];

  assert.deepEqual(
    filterOperationalShiftAuctionOperators(rows).map((row) => row.id),
    [1],
  );
});

test('snapshot hydrates only a clean draft and never crosses a pending save cutoff', () => {
  assert.equal(shouldHydrateShiftAuctionDraft({ dirty: false }), true);
  assert.equal(shouldHydrateShiftAuctionDraft({ dirty: true }), false);

  const pendingSavedAt = '2026-07-30T12:00:00';
  assert.equal(shouldHydrateShiftAuctionDraft({
    dirty: false,
    pendingSavedAt,
    snapshotUpdatedAt: '',
  }), false);
  assert.equal(shouldHydrateShiftAuctionDraft({
    dirty: false,
    pendingSavedAt,
    snapshotUpdatedAt: '2026-07-30T11:59:59',
  }), false);
  assert.equal(shouldHydrateShiftAuctionDraft({
    dirty: false,
    pendingSavedAt,
    snapshotUpdatedAt: pendingSavedAt,
  }), true);
  assert.equal(shouldHydrateShiftAuctionDraft({
    dirty: false,
    pendingSavedAt,
    snapshotUpdatedAt: '2026-07-30T12:00:01',
  }), true);
  assert.equal(shouldHydrateShiftAuctionDraft({
    dirty: false,
    pendingSavedAt: '2026-07-30T12:00:00.123900',
    snapshotUpdatedAt: '2026-07-30T12:00:00.123100',
  }), false);
});

test('в чат-аукционе выбирают из чатников, а не из операторов линии', () => {
  // Список кандидатов один на оба направления (весь справочник сотрудников),
  // поэтому без режима чат-аукцион показывал бы состав ЛИНИИ, а сервер потом
  // молча отбрасывал выбранных — выбор выглядел бы сохранённым, но не работал.
  const people = [
    { id: 1, name: 'Линия', role: 'operator', status: 'working', direction: 'Основа' },
    { id: 2, name: 'Чат', role: 'operator', status: 'working', direction: 'Чат менеджер' },
    { id: 3, name: 'Чат Б/С', role: 'operator', status: 'bs', direction: ' чат менеджер ' },
    { id: 4, name: 'ОП', role: 'operator', status: 'working', direction: 'Основа ОП' },
    { id: 5, name: 'Уволен', role: 'operator', status: 'fired', direction: 'Чат менеджер' },
  ];

  const chat = normalizeShiftAuctionOperators(people, [], 'chat');
  assert.deepEqual(chat.map((row) => row.id).sort(), [2, 3],
    'в чате видны только чатники, включая временные статусы, но без уволенных');

  const line = normalizeShiftAuctionOperators(people, [], 'line');
  assert.deepEqual(line.map((row) => row.id), [1],
    'на линии по-прежнему только «Основа» — «Основа ОП» не считается');

  // Режим по умолчанию — линия: забытый аргумент не должен открывать чужой состав.
  assert.deepEqual(normalizeShiftAuctionOperators(people).map((row) => row.id), [1]);
  assert.deepEqual(normalizeShiftAuctionOperators(people, [], 'мусор').map((row) => row.id), [1]);
});

test('мониторинг и сетка смен считают своими тех же людей, что и селектор', () => {
  const rows = [
    { id: 1, direction: 'Основа', status: 'working' },
    { id: 2, direction: 'Чат менеджер', status: 'working' },
    { id: 3, direction: 'Чат менеджер', status: 'bs' },
    { id: 4, direction: 'ТП чат', status: 'working' },
  ];

  assert.deepEqual(
    filterOperationalShiftAuctionOperators(rows, 'chat').map((row) => row.id),
    [2, 4],
    'в строю у чата только работающие чатники');
  assert.deepEqual(
    filterOperationalShiftAuctionOperators(rows, 'line').map((row) => row.id),
    [1]);
  assert.deepEqual(filterOperationalShiftAuctionOperators(rows).map((row) => row.id), [1]);
});
