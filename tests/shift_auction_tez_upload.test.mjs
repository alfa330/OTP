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
import {
  AUCTION_DEPARTMENTS,
  auctionDepartmentOfDirection,
  auctionDirectionForDepartment,
  readStoredAuctionDirection,
  storeAuctionDirection,
} from '../src/components/resources/shiftAuctionDirectionMemory.js';
import { departmentAllowsView, firstAllowedView } from '../src/utils/departmentViews.js';

// Аукцион Тез КЦ (задача #124): люди выдуманы.

test('«Аукцион смен» открыт Тез КЦ: операторам, СВ и главе отдела', () => {
  const operator = { id: 9001, role: 'operator', department_code: 'tez' };
  const supervisor = { id: 9002, role: 'sv', department_code: 'tez' };
  const head = { id: 9003, role: 'admin', department_code: 'tez', headed_department_id: 560 };
  for (const user of [operator, supervisor, head]) {
    assert.equal(departmentAllowsView(user, 'shift_auction'), true, user.role);
  }
  // Раздел, который оператор Теза видит первым при входе, прежний.
  assert.equal(firstAllowedView(operator), 'profile');
  // Остальным отделам с картой разделов пункт не добавился.
  assert.equal(departmentAllowsView({ id: 9004, role: 'operator', department_code: 'op' }, 'shift_auction'), false);
});

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

// Память выбора управляющего: хранилище браузера подменяем словарём.
const withStorage = (run, { broken = false } = {}) => {
  const previous = globalThis.window;
  const data = new Map();
  globalThis.window = {
    localStorage: {
      getItem: (key) => {
        if (broken) throw new Error('blocked');
        return data.has(key) ? data.get(key) : null;
      },
      setItem: (key, value) => {
        if (broken) throw new Error('blocked');
        data.set(key, String(value));
      },
    },
  };
  try {
    run(data);
  } finally {
    globalThis.window = previous;
  }
};

test('меню «Аукцион смен»: СЗоВ и Тез КЦ со своими прогонами', () => {
  assert.deepEqual(AUCTION_DEPARTMENTS.map((item) => [item.code, item.label]), [['szov', 'СЗоВ'], ['tez', 'Тез КЦ']]);
  assert.equal(auctionDepartmentOfDirection('chat'), 'szov');
  assert.equal(auctionDepartmentOfDirection('tez_op'), 'tez');
  // Неизвестное — линия, как у сервера.
  assert.equal(auctionDepartmentOfDirection('что-то'), 'szov');
});

test('отдел открывается на своём последнем направлении', () => {
  withStorage(() => {
    assert.equal(auctionDirectionForDepartment('szov'), 'line');
    assert.equal(auctionDirectionForDepartment('tez'), 'tez_tp');
    storeAuctionDirection('chat');
    storeAuctionDirection('tez_op');
    // Последнее общее — Тез КЦ, но СЗоВ помнит свой чат.
    assert.equal(readStoredAuctionDirection(), 'tez_op');
    assert.equal(auctionDirectionForDepartment('szov'), 'chat');
    assert.equal(auctionDirectionForDepartment(' TEZ '), 'tez_op');
    // Сервер прислал прогоны отдела без запомненного — берём первый из них.
    assert.equal(auctionDirectionForDepartment('tez', ['tez_tp']), 'tez_tp');
    assert.equal(auctionDirectionForDepartment('hr'), '');
    assert.equal(auctionDirectionForDepartment(''), '');
  });
});

test('закрытое хранилище не ломает выбор отдела', () => {
  withStorage(() => {
    storeAuctionDirection('chat');
    assert.equal(readStoredAuctionDirection(), 'line');
    assert.equal(auctionDirectionForDepartment('tez'), 'tez_tp');
  }, { broken: true });
});

test('испорченная запись памяти — как пустая', () => {
  withStorage((data) => {
    data.set('otp_shift_auction_department_direction_v1', '[1,2]');
    assert.equal(auctionDirectionForDepartment('szov'), 'line');
    data.set('otp_shift_auction_department_direction_v1', '{"szov":"tez_op"}');
    // Чужой прогон в памяти отдела не открывается.
    assert.equal(auctionDirectionForDepartment('szov'), 'line');
    data.set('otp_shift_auction_department_direction_v1', 'не json');
    storeAuctionDirection('chat');
    assert.equal(data.get('otp_shift_auction_department_direction_v1'), '{"szov":"chat"}');
  });
});
