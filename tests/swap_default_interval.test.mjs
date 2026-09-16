/*
 * Интервал по умолчанию при выборе даты в запросе на замену («Мои смены»).
 * Решения из src/components/schedule/swapDefaultInterval.js — без React и данных.
 *
 * Дефект, ради которого модуль появился: селект даты менял только дату, время
 * оставалось от прошлого дня. У оператора со сменами 15:00–00:00 и одной поздней
 * 18:30–01:00 интервал 17:00–18:00 в этот день уже не попадал внутрь смены,
 * форма считалась некорректной и кандидатов на замену не запрашивала вовсе.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  defaultSwapIntervalForDate,
  nightTailIntervalForDate,
  ownShiftIntervalsForDate,
  shiftDayKey,
} from '../src/components/schedule/swapDefaultInterval.js';

const WEEK = {
  '2026-09-13': [{ start: '15:00', end: '00:00' }],
  '2026-09-14': [{ start: '18:30', end: '01:00' }],
  '2026-09-15': [{ start: '15:30', end: '00:00' }],
  '2026-09-18': [{ start: '14:30', end: '15:00' }, { start: '20:00', end: '20:30' }],
  '2026-09-20': [{ start: '23:30', end: '07:00' }],
};

test('смены дня в минутах: ночной хвост уезжает за 1440, а не обрезается', () => {
  assert.deepEqual(ownShiftIntervalsForDate(WEEK, '2026-09-14'), [{ start: 1110, end: 1500 }]);
  assert.deepEqual(ownShiftIntervalsForDate(WEEK, '2026-09-13'), [{ start: 900, end: 1440 }]);
  assert.deepEqual(ownShiftIntervalsForDate(WEEK, '2026-09-18'), [
    { start: 870, end: 900 },
    { start: 1200, end: 1230 },
  ]);
  assert.deepEqual(ownShiftIntervalsForDate(WEEK, '2026-09-19'), []);
  assert.deepEqual(ownShiftIntervalsForDate(null, '2026-09-14'), []);
});

test('хвост вчерашней смены виден в дне, только если смена перешла полночь', () => {
  assert.deepEqual(nightTailIntervalForDate(WEEK, '2026-09-15'), { start: 0, end: 60 });
  assert.equal(nightTailIntervalForDate(WEEK, '2026-09-14'), null, 'смена 15:00–00:00 хвоста не даёт');
  assert.equal(nightTailIntervalForDate(WEEK, 'не дата'), null);
});

test('время едет за датой: у каждого дня свой интервал, а не прошлого дня', () => {
  assert.deepEqual(defaultSwapIntervalForDate(WEEK, '2026-09-13'),
    { endDate: '2026-09-13', startTime: '15:00', endTime: '16:00' });
  assert.deepEqual(defaultSwapIntervalForDate(WEEK, '2026-09-14'),
    { endDate: '2026-09-14', startTime: '18:30', endTime: '19:30' });
  assert.deepEqual(defaultSwapIntervalForDate(WEEK, '2026-09-15'),
    { endDate: '2026-09-15', startTime: '15:30', endTime: '16:30' });
});

test('своя смена дня важнее ночного хвоста предыдущей', () => {
  // У 15.09 есть и хвост 00:00–01:00 от 14.09, и своя смена с 15:30.
  assert.equal(defaultSwapIntervalForDate(WEEK, '2026-09-15').startTime, '15:30');
});

test('день без своих смен отдаёт ночной хвост — передавать там больше нечего', () => {
  const tailOnly = { '2026-09-10': [{ start: '15:00', end: '01:00' }] };
  assert.deepEqual(defaultSwapIntervalForDate(tailOnly, '2026-09-11'),
    { endDate: '2026-09-11', startTime: '00:00', endTime: '01:00' });
});

test('несколько смен в дне — берём первую по времени', () => {
  assert.deepEqual(defaultSwapIntervalForDate(WEEK, '2026-09-18'),
    { endDate: '2026-09-18', startTime: '14:30', endTime: '15:00' });
});

test('час после полуночи переносит и дату окончания', () => {
  assert.deepEqual(defaultSwapIntervalForDate(WEEK, '2026-09-20'),
    { endDate: '2026-09-21', startTime: '23:30', endTime: '00:30' });
});

test('целую смену берут без подрезки: capMinutes = 0', () => {
  assert.deepEqual(defaultSwapIntervalForDate(WEEK, '2026-09-14', 0),
    { endDate: '2026-09-15', startTime: '18:30', endTime: '01:00' });
});

test('дня нет в графике — форма остаётся пустой, а не с чужим временем', () => {
  assert.deepEqual(defaultSwapIntervalForDate(WEEK, '2026-09-30'),
    { endDate: '2026-09-30', startTime: '', endTime: '' });
  assert.deepEqual(defaultSwapIntervalForDate(WEEK, ''),
    { endDate: '', startTime: '', endTime: '' });
});

test('соседний день ключом', () => {
  assert.equal(shiftDayKey('2026-09-01', -1), '2026-08-31');
  assert.equal(shiftDayKey('2026-12-31', 1), '2027-01-01');
  assert.equal(shiftDayKey('31.12.2026', 1), '');
});
