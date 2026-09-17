/*
 * Правила телефонного вида «Расчета ресурсов» и планировщика графиков.
 *
 * Разметка проверяется стражами tests/test_resource_fte_mobile.py и кадрами на
 * стенде; здесь — то, в чём легко ошибиться молча: подписи периодов через
 * границу года, календарь с понедельника, выбор периода двумя касаниями, окно
 * времени биллинга и время смены через полночь.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  addPhoneDays,
  buildPhoneCalendarMonth,
  formatPhoneDayTitle,
  formatPhoneDuration,
  formatPhoneRange,
  formatPhoneShortDate,
  nextPhoneRangeSelection,
  orderPhoneTimeRange,
  phoneMinutesToClock,
  phoneShiftEditRange,
  phoneTabLabel,
  phoneTileSpans,
} from '../src/components/resources/resourceFtePhone.js';

test('период в одном году пишется без года', () => {
  assert.equal(formatPhoneRange('2026-09-21', '2026-09-27'), '21.09 — 27.09');
});

test('период через Новый год пишется с годами — иначе читался бы назад во времени', () => {
  assert.equal(formatPhoneRange('2026-12-28', '2027-01-03'), '28.12.2026 — 03.01.2027');
});

test('период из одного дня и пустой период', () => {
  assert.equal(formatPhoneRange('2026-09-21', '2026-09-21'), '21.09');
  assert.equal(formatPhoneRange('', ''), '—');
  assert.equal(formatPhoneRange('2026-09-21', ''), '21.09');
  assert.equal(formatPhoneShortDate('мусор'), '—');
});

test('заголовок дня — день недели и месяц в родительном падеже', () => {
  assert.equal(formatPhoneDayTitle('2026-09-21'), 'Пн, 21 сентября');
  assert.equal(formatPhoneDayTitle('2026-03-08'), 'Вс, 8 марта');
});

test('сдвиг даты не уводит день через UTC', () => {
  assert.equal(addPhoneDays('2026-09-27', 7), '2026-10-04');
  assert.equal(addPhoneDays('2026-03-01', -1), '2026-02-28');
});

test('нечётная последняя плитка растягивается, чётные — нет', () => {
  assert.deepEqual(phoneTileSpans(5), [false, false, false, false, true]);
  assert.deepEqual(phoneTileSpans(4), [false, false, false, false]);
  assert.deepEqual(phoneTileSpans(1), [true]);
  assert.deepEqual(phoneTileSpans(0), []);
});

test('календарь — шесть недель с понедельника, чужие дни помечены', () => {
  const weeks = buildPhoneCalendarMonth(new Date(2026, 8, 1));
  assert.equal(weeks.length, 6);
  assert.ok(weeks.every((week) => week.length === 7));
  // 1 сентября 2026 — вторник: первая клетка — понедельник 31 августа.
  assert.deepEqual(weeks[0][0], { iso: '2026-08-31', day: 31, outside: true });
  assert.deepEqual(weeks[0][1], { iso: '2026-09-01', day: 1, outside: false });
  const september = weeks.flat().filter((cell) => !cell.outside);
  assert.equal(september.length, 30);
});

test('период выбирается двумя касаниями, касание раньше начала меняет концы местами', () => {
  const first = nextPhoneRangeSelection('', '2026-09-21');
  assert.deepEqual(first, { draftStart: '2026-09-21', range: null });
  assert.deepEqual(nextPhoneRangeSelection(first.draftStart, '2026-09-27'), { draftStart: '', range: ['2026-09-21', '2026-09-27'] });
  assert.deepEqual(nextPhoneRangeSelection(first.draftStart, '2026-09-14'), { draftStart: '', range: ['2026-09-14', '2026-09-21'] });
  assert.deepEqual(nextPhoneRangeSelection('2026-09-21', '2026-09-21'), { draftStart: '', range: ['2026-09-21', '2026-09-21'] });
});

test('окно времени биллинга: сдвинутый край тянет второй, как на компьютере', () => {
  assert.deepEqual(orderPhoneTimeRange('start', '06:00', '00:00', '23:59'), ['06:00', '23:59']);
  assert.deepEqual(orderPhoneTimeRange('start', '14:00', '06:00', '12:00'), ['14:00', '14:00']);
  assert.deepEqual(orderPhoneTimeRange('end', '05:00', '06:00', '12:00'), ['05:00', '05:00']);
  assert.deepEqual(orderPhoneTimeRange('end', '23:59', '06:00', '12:00'), ['06:00', '23:59']);
  // Пустое поле (стёрли время) не ломает окно.
  assert.deepEqual(orderPhoneTimeRange('start', '', '06:00', '12:00'), ['06:00', '12:00']);
});

test('смена с концом раньше начала — ночная, до следующего дня', () => {
  assert.deepEqual(phoneShiftEditRange('20:00', '08:00'), {
    valid: true, error: '', startMinute: 1200, endMinute: 1920, overnight: true,
  });
  assert.deepEqual(phoneShiftEditRange('09:00', '18:00'), {
    valid: true, error: '', startMinute: 540, endMinute: 1080, overnight: false,
  });
});

test('ограничения смены — те же, что у перетаскивания в планировщике', () => {
  assert.equal(phoneShiftEditRange('09:00', '09:30').valid, false, 'короче часа нельзя');
  assert.equal(phoneShiftEditRange('09:00', '09:00').valid, false, 'равные концы — это сутки, дальше 08:00 следующего дня');
  assert.equal(phoneShiftEditRange('21:00', '09:00').valid, false, 'позже 08:00 следующего дня нельзя');
  assert.equal(phoneShiftEditRange('', '09:00').valid, false);
  assert.match(phoneShiftEditRange('09:00', '09:30').error, /часа/);
});

test('минуты в часы и длительность', () => {
  assert.equal(phoneMinutesToClock(1920), '08:00');
  assert.equal(phoneMinutesToClock(-30), '23:30');
  assert.equal(formatPhoneDuration(510), '8 ч 30 мин');
  assert.equal(formatPhoneDuration(540), '9 ч');
  assert.equal(formatPhoneDuration(45), '45 мин');
});

test('на телефоне «Биллинг Oktell» называется просто «Биллинг»', () => {
  assert.equal(phoneTabLabel({ key: 'oktell_billing', label: 'Биллинг Oktell' }), 'Биллинг');
  assert.equal(phoneTabLabel({ key: 'overview', label: 'Обзор' }), 'Обзор');
});
