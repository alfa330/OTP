import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isShiftAuctionDirection,
  normalizeShiftAuctionDirectionMode,
  normalizeShiftAuctionOperators,
} from '../src/components/resources/shiftAuctionParticipants.js';
import {
  formatUploadDayLabel,
  formatUploadHours,
  formatUploadPeriod,
  formatUploadShift,
  formatUploadShiftCount,
  formatUploadWhen,
  uploadDayChanged,
  uploadDayShiftTones,
} from '../src/components/resources/shiftAuctionUpload.js';

// Аукцион Тез КЦ (задача #124): люди выдуманы.

test('линии Тез КЦ — свои прогоны: кандидаты только своего направления', () => {
  const people = [
    { id: 1, name: 'ТП один', role: 'operator', status: 'working', direction: 'ТП линия' },
    { id: 2, name: 'ТП два', role: 'operator', status: 'bs', direction: ' тп  ЛИНИЯ ' },
    { id: 3, name: 'ОП один', role: 'operator', status: 'working', direction: 'ОП линия' },
    { id: 4, name: 'Линия СЗоВ', role: 'operator', status: 'working', direction: 'Основа' },
    { id: 5, name: 'Чат СЗоВ', role: 'operator', status: 'working', direction: 'Чат менеджер' },
  ];
  assert.deepEqual(normalizeShiftAuctionOperators(people, [], 'tez_tp').map((row) => row.id), [2, 1]);
  assert.deepEqual(normalizeShiftAuctionOperators(people, [], 'tez_op').map((row) => row.id), [3]);
  // СЗоВ не сдвинулся: линия и чат видят прежних людей.
  assert.deepEqual(normalizeShiftAuctionOperators(people, [], 'line').map((row) => row.id), [4]);
  assert.deepEqual(normalizeShiftAuctionOperators(people, [], 'chat').map((row) => row.id), [5]);
  assert.equal(isShiftAuctionDirection('ТП линия', 'line'), false);
});

test('неизвестный режим — линия, как у сервера', () => {
  assert.equal(normalizeShiftAuctionDirectionMode('TEZ_TP'), 'tez_tp');
  assert.equal(normalizeShiftAuctionDirectionMode('tez'), 'line');
  assert.equal(normalizeShiftAuctionDirectionMode(''), 'line');
});

test('подписи сверки файла', () => {
  assert.equal(formatUploadDayLabel('2030-11-18'), 'пн, 18.11');
  assert.equal(formatUploadPeriod('2030-11-18', '2030-11-24'), '18.11–24.11 · 7 дней');
  assert.equal(formatUploadPeriod('2030-11-18', '2030-11-18'), '18.11–18.11 · 1 день');
  assert.equal(formatUploadPeriod('2030-11-18', '2030-11-21'), '18.11–21.11 · 4 дня');
  assert.equal(formatUploadHours(22650), '377,5');
  assert.equal(formatUploadHours(600), '10');
  assert.equal(formatUploadShift({ start: '13:00', end: '19:30' }), '13–19:30');
  assert.equal(formatUploadShift({ start: '21:00', end: '09:00' }), '21–9');
  assert.equal(formatUploadWhen('2030-11-12T13:47:05'), '12.11 в 13:47');
  for (const [count, text] of [[1, '1 смена'], [2, '2 смены'], [5, '5 смен'], [11, '11 смен'], [21, '21 смена'], [54, '54 смены'], [56, '56 смен']]) {
    assert.equal(formatUploadShiftCount(count), text);
  }
});

test('«добавится» отмечается поштучно, а не по совпадению времени', () => {
  const day = {
    shifts: [{ start: '09:00', end: '13:00' }, { start: '09:00', end: '13:00' }, { start: '21:00', end: '09:00' }],
    added: [{ start: '09:00', end: '13:00' }],
    removed: [],
  };
  assert.deepEqual(uploadDayShiftTones(day).map((item) => item.tone), ['added', 'plain', 'plain']);
  assert.equal(uploadDayChanged(day), true);
  assert.equal(uploadDayChanged({ shifts: day.shifts, added: [], removed: [] }), false);
});
