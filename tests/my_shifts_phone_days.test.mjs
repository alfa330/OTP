/*
 * «Мои смены» на телефоне: подписи дней в полосе и порядок коллег в списке дня.
 * Решения из src/components/schedule/myShiftsPhoneDays.js — без React и данных.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  colleaguesForPhoneDay,
  compactClock,
  describeColleaguesPhoneDay,
  describeMyShiftsPhoneDay,
  formatPhoneWeekLabel,
  pickPhoneDayDate,
  shortShiftCaption,
} from '../src/components/schedule/myShiftsPhoneDays.js';

test('время в полосе дней — без ведущего нуля и без «:00»', () => {
  assert.equal(compactClock('09:00'), '9');
  assert.equal(compactClock('09:30'), '9:30');
  assert.equal(compactClock('18:00'), '18');
  assert.equal(compactClock('00:00'), '0');
  assert.equal(shortShiftCaption({ start: '09:00', end: '18:00' }), '9–18');
  assert.equal(shortShiftCaption({ start: '20:00', end: '08:00' }), '20–8');
});

test('подпись дня: смена важнее статуса, статус важнее выходного', () => {
  assert.deepEqual(describeMyShiftsPhoneDay(null), { caption: '', tone: 'none' });
  assert.deepEqual(
    describeMyShiftsPhoneDay({ shifts: [{ start: '09:00', end: '18:00' }], isDayOff: true }),
    { caption: '9–18', tone: 'shift' },
  );
  assert.deepEqual(
    describeMyShiftsPhoneDay({ shifts: [{ start: '09:00', end: '13:00' }, { start: '15:00', end: '19:00' }] }),
    { caption: '2 смены', tone: 'shift' },
  );
  assert.deepEqual(
    describeMyShiftsPhoneDay({ shifts: [], scheduleStatus: { label: 'Отпуск' }, isDayOff: true }),
    { caption: 'Отпуск', tone: 'blocked' },
  );
  assert.deepEqual(describeMyShiftsPhoneDay({ shifts: [], isDayOff: true }), { caption: 'Вых.', tone: 'off' });
  assert.deepEqual(describeMyShiftsPhoneDay({ shifts: [], isDayOff: false }), { caption: '—', tone: 'none' });
});

test('выбранный день держится в видимой неделе: свой → сегодня → первый', () => {
  const week = ['2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18', '2026-09-19', '2026-09-20'];
  assert.equal(pickPhoneDayDate('2026-09-16', week, '2026-09-14'), '2026-09-16');
  assert.equal(pickPhoneDayDate('2026-09-09', week, '2026-09-14'), '2026-09-14');
  assert.equal(pickPhoneDayDate('2026-09-09', week, '2026-09-30'), '2026-09-14');
  assert.equal(pickPhoneDayDate('2026-09-09', [], '2026-09-30'), '2026-09-09');
});

test('подпись недели: один месяц — одним словом, стык месяцев — двумя', () => {
  assert.equal(formatPhoneWeekLabel(['2026-09-14', '2026-09-15', '2026-09-20']), '14 — 20 сентября');
  assert.equal(formatPhoneWeekLabel(['2026-09-28', '2026-10-04']), '28 сентября — 4 октября');
  assert.equal(formatPhoneWeekLabel(['2026-09-14']), '14 сентября');
  assert.equal(formatPhoneWeekLabel([]), '');
});

test('коллеги на день: работающие первыми по началу смены, потом выходные, потом без смен', () => {
  const operators = [
    { id: 1, name: 'Яковлев', shifts: {}, daysOff: [] },
    { id: 2, name: 'Борисова', shifts: { '2026-09-14': [{ start: '12:00', end: '21:00' }] }, daysOff: [] },
    { id: 3, name: 'Алиев', shifts: {}, daysOff: ['2026-09-14'] },
    { id: 4, name: 'Громов', shifts: { '2026-09-14': [{ start: '09:00', end: '18:00' }] }, daysOff: [] },
  ];
  const rows = colleaguesForPhoneDay(operators, '2026-09-14');
  assert.deepEqual(rows.map((row) => row.op.name), ['Громов', 'Борисова', 'Алиев', 'Яковлев']);
  assert.equal(rows[2].isDayOff, true);
  assert.equal(rows[3].isDayOff, false);
  assert.deepEqual(describeColleaguesPhoneDay(operators, '2026-09-14'), { caption: '2 чел.', tone: 'none' });
  assert.deepEqual(describeColleaguesPhoneDay(operators, '2026-09-15'), { caption: '—', tone: 'none' });
});
