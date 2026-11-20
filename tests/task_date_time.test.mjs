import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_DUE_TIME, LATE_DUE_TIME, dayAfter, deadlineDurationFields, deadlinePresetActive, deadlinePresetValue,
  deadlineSummary, isLocalDateTimePast, joinLocalDateTime, localDayIso, minutesBetweenLocal, parseLocalDateTime,
  shiftLocalDateTime, splitLocalDateTime,
} from '../src/components/tasks/taskDateTime.js';

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

/* ── Дедлайн днём и временем (форма задачи) ── */
// Четверг, 19 ноября 2026, 10:15 по местным часам.
const MORNING = new Date(2026, 10, 19, 10, 15);
const EVENING = new Date(2026, 10, 19, 19, 40);

test('строка поля разбирается как местное время, мусор и несуществующий день — не срок', () => {
  assert.equal(parseLocalDateTime('2026-11-20T09:30').getTime(), new Date(2026, 10, 20, 9, 30).getTime());
  // День без времени — конец рабочего дня, как и при сборке строки.
  assert.equal(parseLocalDateTime('2026-11-20').getTime(), new Date(2026, 10, 20, 18, 0).getTime());
  ['', null, 'завтра', '2026-02-31T10:00', '2026-13-01T10:00'].forEach((value) => {
    assert.equal(parseLocalDateTime(value), null, String(value));
  });
});

test('прошедший срок узнаётся по местным часам, будущий — нет', () => {
  assert.equal(isLocalDateTimePast('2026-11-19T10:00', MORNING), true);
  assert.equal(isLocalDateTimePast('2026-11-19T10:15', MORNING), true, 'срок «прямо сейчас» уже наступил');
  assert.equal(isLocalDateTimePast('2026-11-19T10:20', MORNING), false);
  // Ближе минуты — тоже: сервер округлил бы такой отступ до нуля.
  const late = new Date(2026, 10, 19, 17, 59, 30);
  assert.equal(isLocalDateTimePast('2026-11-19T18:00', late), true, '30 секунд до срока');
  assert.equal(isLocalDateTimePast('2026-11-19T18:01', late), false, 'полторы минуты до срока');
  // «Сегодня» в 17:59:30 уже не даёт 18:00 — только конец дня.
  assert.equal(deadlinePresetValue(0, '', late), '2026-11-19T23:59');
  assert.equal(isLocalDateTimePast('', MORNING), false, 'без срока нечему наступать');
});

test('быстрые сроки ставят день по календарю, время по умолчанию — конец рабочего дня', () => {
  assert.equal(deadlinePresetValue(0, '', MORNING), '2026-11-19T18:00');
  assert.equal(deadlinePresetValue(1, '', MORNING), '2026-11-20T18:00');
  assert.equal(deadlinePresetValue(3, '', MORNING), '2026-11-22T18:00');
  assert.equal(deadlinePresetValue(7, '', MORNING), '2026-11-26T18:00');
  // Через границу месяца и года — днями календаря, а не «плюс 24 часа».
  assert.equal(deadlinePresetValue(7, '', new Date(2026, 11, 28, 9, 0)), '2027-01-04T18:00');
});

test('быстрый срок меняет только день: выбранное время остаётся', () => {
  assert.equal(deadlinePresetValue(1, '2026-11-25T10:30', MORNING), '2026-11-20T10:30');
  assert.equal(deadlinePresetValue(7, '2026-11-20T09:05', EVENING), '2026-11-26T09:05');
});

test('«Сегодня» никогда не ставит срок в прошлом', () => {
  // Выбранное время сегодня уже прошло — конец рабочего дня.
  assert.equal(deadlinePresetValue(0, '2026-11-21T09:00', MORNING), '2026-11-19T18:00');
  // Позже конца рабочего дня — до конца дня.
  assert.equal(LATE_DUE_TIME, '23:59');
  assert.equal(deadlinePresetValue(0, '', EVENING), '2026-11-19T23:59');
  assert.equal(deadlinePresetValue(0, '2026-11-20T21:00', EVENING), '2026-11-19T21:00');
  assert.equal(isLocalDateTimePast(deadlinePresetValue(0, '', EVENING), EVENING), false);
});

test('подсвечена быстрая кнопка того дня, на котором стоит срок', () => {
  assert.equal(deadlinePresetActive(1, '2026-11-20T09:00', MORNING), true);
  assert.equal(deadlinePresetActive(0, '2026-11-20T09:00', MORNING), false);
  assert.equal(deadlinePresetActive(0, '', MORNING), false, 'без срока не подсвечено ничего');
  assert.equal(dayAfter(3, MORNING), '2026-11-22');
  assert.equal(localDayIso(MORNING), '2026-11-19');
});

test('на чипе срок словами: сегодня, завтра, иначе день недели и число', () => {
  assert.equal(deadlineSummary('2026-11-19T18:00', MORNING), 'Сегодня, 18:00');
  assert.equal(deadlineSummary('2026-11-20T10:30', MORNING), 'Завтра, 10:30');
  assert.equal(deadlineSummary('2026-11-27T18:00', MORNING), 'Пт, 27 нояб., 18:00');
  // Другой год — с годом, чтобы не спутать с ближайшим январём.
  assert.match(deadlineSummary('2027-01-04T09:00', MORNING), /^Пн, 4 янв\. 2027.*, 09:00$/);
  assert.equal(deadlineSummary('', MORNING), '');
});

/* ── Повторяющаяся задача: срок — смещение от начала повтора ── */

test('сдвиг и разница считаются по стенным часам, через сутки, месяц и год', () => {
  assert.equal(shiftLocalDateTime('2026-11-20T10:00', 8 * 60), '2026-11-20T18:00');
  assert.equal(shiftLocalDateTime('2026-12-31T22:00', 3 * 60), '2027-01-01T01:00');
  assert.equal(shiftLocalDateTime('', 60), '');
  assert.equal(minutesBetweenLocal('2026-11-20T10:00', '2026-11-21T18:00'), 32 * 60);
  assert.equal(minutesBetweenLocal('2026-11-20T10:00', '2026-11-20T09:00'), -60);
  assert.equal(minutesBetweenLocal('2026-11-20T10:00', ''), null);
  // Туда и обратно — то же смещение.
  assert.equal(minutesBetweenLocal('2026-02-27T23:30', shiftLocalDateTime('2026-02-27T23:30', 5000)), 5000);
});

test('смещение раскладывается в поля «через N» сервера: часы до 23, минуты до 59', () => {
  assert.deepEqual(deadlineDurationFields(32 * 60 + 5), { deadline_days: '1', deadline_hours: '8', deadline_minutes: '5' });
  assert.deepEqual(deadlineDurationFields(0), { deadline_days: '0', deadline_hours: '0', deadline_minutes: '0' });
  assert.deepEqual(deadlineDurationFields(-30), { deadline_days: '0', deadline_hours: '0', deadline_minutes: '0' });
});
