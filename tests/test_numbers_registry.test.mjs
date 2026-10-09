import test from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_PERIOD_DAYS, countsByNumber, dayLabel, daySpan, daysOf, durationLabel, filterItems, itemSummary,
  lineCaption, numberCaption, periodError, periodPresets, shiftDay, splitLabel, testsLabel, timeLabel,
} from '../src/components/test_numbers/testNumbersMeta.js';

/* «Реестр тестовых номеров» — подписи и подсчёты экрана. Сервер отдаёт тесты
 * списком (test_numbers/activity.py); счёт по дням и отбор по номеру делает экран
 * тем же правилом, что сервер: один звонок или одна переписка за сутки — один тест. */

const call = (id, day, extra = {}) => ({
  id, kind: 'call', day, at: `${day}T10:32:00`, phone_key: '7000000101', ...extra,
});
const chat = (id, day, extra = {}) => ({
  id, kind: 'chat', day, at: `${day}T11:05:00`, phone_key: '7000000102', ...extra,
});

test('склонение тестов, звонков и чатов', () => {
  assert.equal(testsLabel(1), '1 тест');
  assert.equal(testsLabel(3), '3 теста');
  assert.equal(testsLabel(11), '11 тестов');
  assert.equal(testsLabel(21), '21 тест');
  assert.equal(splitLabel({ calls: 2, chats: 0 }), '2 звонка');
  assert.equal(splitLabel({ calls: 0, chats: 5 }), '5 чатов');
  assert.equal(splitLabel({ calls: 1, chats: 1 }), '1 звонок · 1 чат');
});

test('дни по убыванию, без пустых, звонки и чаты раздельно', () => {
  const days = daysOf([call('a', '2026-10-07'), chat('b', '2026-10-08'), call('c', '2026-10-08')]);
  assert.deepEqual(days.map((d) => [d.day, d.calls, d.chats, d.total]),
    [['2026-10-08', 1, 1, 2], ['2026-10-07', 1, 0, 1]]);
  assert.deepEqual(days[0].items.map((i) => i.id), ['b', 'c']);
  assert.deepEqual(daysOf([]), []);
});

test('отбор по номеру и счёт на номер', () => {
  const items = [call('a', '2026-10-07'), chat('b', '2026-10-08'), call('c', '2026-10-08')];
  assert.deepEqual(filterItems(items, '7000000101').map((i) => i.id), ['a', 'c']);
  assert.equal(filterItems(items, '').length, 3);
  assert.deepEqual(countsByNumber(items), { 7000000101: 2, 7000000102: 1 });
});

test('подписи дня и времени', () => {
  assert.equal(dayLabel('2026-10-09', '2026-10-09'), 'Сегодня');
  assert.equal(dayLabel('2026-10-08', '2026-10-09'), 'Вчера');
  assert.equal(dayLabel('2026-10-01', '2026-10-09'), 'чт, 1 окт');
  assert.equal(dayLabel('2025-12-31', '2026-10-09'), 'ср, 31 дек 2025');
  assert.equal(timeLabel('2026-10-09T08:05:41'), '08:05');
  assert.equal(durationLabel(84), '1:24');
  assert.equal(durationLabel(0), '0:00');
});

test('строка теста не печатает пустых частей', () => {
  assert.equal(itemSummary(call('a', '2026-10-08', { direction: 'in', result: 'Отвечен', duration_seconds: 84 })),
    'входящий · Отвечен · 1:24');
  assert.equal(itemSummary(call('a', '2026-10-08', { direction: 'out', duration_seconds: 0 })), 'исходящий');
  assert.equal(itemSummary(chat('b', '2026-10-08', { messages: 5 })), '5 сообщений');
  assert.equal(itemSummary(chat('b', '2026-10-08', { messages: null })), '');
});

test('период — не длиннее, чем принимает сервер', () => {
  assert.equal(MAX_PERIOD_DAYS, 31);
  assert.equal(daySpan('2026-10-01', '2026-10-31'), 31);
  assert.equal(periodError('2026-10-01', '2026-10-31'), '');
  assert.match(periodError('2026-09-30', '2026-10-31'), /не больше 31/);
  assert.match(periodError('', '2026-10-31'), /Выберите/);
  const presets = periodPresets('2026-10-09');
  assert.deepEqual(presets.map((p) => p.label), ['7 дней', '14 дней', '30 дней']);
  assert.deepEqual(presets[1].range(), { from: '2026-09-26', to: '2026-10-09' });
  for (const preset of presets) {
    const { from, to } = preset.range();
    assert.equal(periodError(from, to), '', preset.label);
  }
  assert.equal(shiftDay('2026-03-01', -1), '2026-02-28');
});

test('номер с владельцем одной строкой', () => {
  assert.equal(numberCaption({ phone_display: '+7 700 000 01 01', owner: { name: 'Иванова Айгерим' } }),
    '+7 700 000 01 01 · Иванова Айгерим');
  assert.equal(numberCaption({ phone_display: '+7 700 000 01 01', owner: null }), '+7 700 000 01 01');
  assert.equal(numberCaption(null), '');
});

test('наш номер и таксопарк теста: «на» у входящего, «с» у исходящего', () => {
  assert.equal(lineCaption({ direction: 'in', line: '+7 700 122 33 22', park: 'Jana такси' }),
    'на +7 700 122 33 22 · Jana такси');
  assert.equal(lineCaption({ direction: 'out', line: '+7 747 577 77 78', park: 'Центр регистрации' }),
    'с +7 747 577 77 78 · Центр регистрации');
  // Исходящий Oktell — линии нет, только парк; Binotel — номер без названия.
  assert.equal(lineCaption({ direction: 'out', line: null, park: 'iTaxi' }), 'iTaxi');
  assert.equal(lineCaption({ direction: 'in', line: '+7 700 300 07 70', park: null }), 'на +7 700 300 07 70');
  assert.equal(lineCaption({ direction: 'in', line: '', park: '' }), '');
  assert.equal(lineCaption(null), '');
});
