/*
 * «Графики работы» на телефоне: что показать в ячейке «человек × день», как
 * подписать период и как собрать список отбора.
 * Решения из src/components/schedule/workSchedulesPhone.js — без React и без
 * данных портала.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  formatPlannerPhoneHours,
  groupPlannerPhoneOperators,
  pickPlannerPhoneDate,
  plannerPhoneCellCaption,
  plannerPhoneDayCell,
  plannerPhoneFilterValue,
  plannerPhoneOnShiftCount,
  plannerPhoneOperatorSummary,
  plannerPhonePeriodLabel,
  plannerPhonePeriodSummary,
  plannerPhoneShiftText,
  plannerPhoneStripCaption,
  sortPlannerPhoneOptions,
} from '../src/components/schedule/workSchedulesPhone.js';

const OP = {
  id: 7,
  name: 'Бекова А.',
  direction: 'Основа',
  shifts: {
    '2026-09-14': [{ start: '09:00', end: '18:00' }],
    '2026-09-15': [{ start: '20:00', end: '02:00' }],
    '2026-09-17': [{ start: '09:00', end: '13:00' }, { start: '15:00', end: '19:00' }],
  },
  daysOff: ['2026-09-18'],
};

test('время смены: ночная помечена «+1»', () => {
  assert.equal(plannerPhoneShiftText({ start: '09:00', end: '18:00' }), '09:00 — 18:00');
  assert.equal(plannerPhoneShiftText({ start: '20:00', end: '02:00' }), '20:00 — 02:00 +1');
  // Смена ровно до полуночи следующий день не задевает — «+1» ей не нужен.
  assert.equal(plannerPhoneShiftText({ start: '16:00', end: '00:00' }), '16:00 — 00:00');
});

test('часы: десятые через запятую, ноль — прочерк', () => {
  assert.equal(formatPlannerPhoneHours(540), '9 ч');
  assert.equal(formatPlannerPhoneHours(465), '7,8 ч');
  assert.equal(formatPlannerPhoneHours(0), '—');
});

test('ночная смена принадлежит дню начала — в «Неделе» она не двоится', () => {
  const start = plannerPhoneDayCell(OP, '2026-09-15', { viewMode: 'week' });
  assert.equal(start.labels.length, 1);
  assert.equal(start.workMin, 360);

  const next = plannerPhoneDayCell(OP, '2026-09-16', { viewMode: 'week' });
  assert.equal(next.labels.length, 0);
  assert.equal(next.hasShift, false);
});

test('в «Дне» хвост вчерашней ночной виден — как в настольной ячейке', () => {
  const next = plannerPhoneDayCell(OP, '2026-09-16', { viewMode: 'day' });
  assert.equal(next.labels.length, 1);
  assert.equal(next.labels[0].carry, true);
  // Своих смен в этот день нет — часы считаем по хвосту.
  assert.equal(next.workMin, 360);
});

test('подпись ячейки: статус важнее смены, смен больше одной — числом', () => {
  assert.deepEqual(
    plannerPhoneCellCaption(plannerPhoneDayCell(OP, '2026-09-14', { viewMode: 'week' })),
    { caption: '09:00 — 18:00', tone: 'shift' },
  );
  assert.deepEqual(
    plannerPhoneCellCaption(plannerPhoneDayCell(OP, '2026-09-17', { viewMode: 'week' })),
    { caption: '2 смены', tone: 'shift' },
  );
  assert.deepEqual(
    plannerPhoneCellCaption(plannerPhoneDayCell(OP, '2026-09-18', { viewMode: 'week' })),
    { caption: 'Выходной', tone: 'off' },
  );
  assert.deepEqual(
    plannerPhoneCellCaption(plannerPhoneDayCell(OP, '2026-09-14', { viewMode: 'week', status: { label: 'Отпуск' } })),
    { caption: 'Отпуск', tone: 'blocked' },
  );
  assert.deepEqual(plannerPhoneCellCaption(null), { caption: '—', tone: 'none' });
});

test('сводка периода: смены, часы и выходные', () => {
  const range = ['2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18'];
  assert.deepEqual(plannerPhoneOperatorSummary(OP, range), { shifts: 4, workMin: 540 + 360 + 240 + 240, daysOff: 1 });
  const total = plannerPhonePeriodSummary([OP, { ...OP, id: 8 }], range);
  assert.equal(total.people, 2);
  assert.equal(total.shifts, 8);
  assert.equal(total.daysOff, 2);
});

test('подпись дня в полосе — сколько человек выходит', () => {
  assert.equal(plannerPhoneOnShiftCount([OP, { id: 9, shifts: {} }], '2026-09-14'), 1);
  assert.equal(plannerPhoneStripCaption(1), '1 чел.');
  assert.equal(plannerPhoneStripCaption(0), '—');
});

test('люди сгруппированы по направлению, порядок раздела не переставляется', () => {
  const groups = groupPlannerPhoneOperators([
    { id: 1, name: 'Б', direction: 'Основа' },
    { id: 2, name: 'А', direction: 'Чат' },
    { id: 3, name: 'В', direction: 'Основа' },
    { id: 4, name: 'Г', direction: '' },
  ]);
  assert.deepEqual(groups.map(g => g.label), ['Основа', 'Чат', 'Без направления']);
  assert.deepEqual(groups[0].items.map(op => op.name), ['Б', 'В']);
});

test('подпись периода: день, неделя и месяц', () => {
  assert.equal(plannerPhonePeriodLabel('day', ['2026-09-18']), '18 сентября');
  assert.equal(plannerPhonePeriodLabel('week', ['2026-09-14', '2026-09-20']), '14 — 20 сентября');
  assert.equal(plannerPhonePeriodLabel('week', ['2026-09-28', '2026-10-04']), '28 сентября — 4 октября');
  assert.equal(plannerPhonePeriodLabel('month', ['2026-09-01', '2026-09-30']), 'Сентябрь 2026');
});

test('выбранный день не выходит за период', () => {
  const week = ['2026-09-14', '2026-09-15', '2026-09-16'];
  assert.equal(pickPlannerPhoneDate('2026-09-15', week, '2026-09-16'), '2026-09-15');
  assert.equal(pickPlannerPhoneDate('2026-09-01', week, '2026-09-16'), '2026-09-16');
  assert.equal(pickPlannerPhoneDate('2026-09-01', week, '2026-10-01'), '2026-09-14');
});

test('строка отбора: одного показываем по имени, иначе числом', () => {
  const options = [{ value: '1', label: 'Бекова А.' }, { value: '2', label: 'Иванов И.' }];
  assert.equal(plannerPhoneFilterValue([], options, 'Все сотрудники'), 'Все сотрудники');
  assert.equal(plannerPhoneFilterValue(['1'], options, 'Все'), 'Бекова А.');
  assert.equal(plannerPhoneFilterValue(['1', '2'], options, 'Все'), 'Выбрано: 2');
});

test('в списке отбора отмеченные стоят первыми, поиск не теряет их', () => {
  const options = [
    { value: '1', label: 'Абаева' },
    { value: '2', label: 'Бекова' },
    { value: '3', label: 'Викторова' },
  ];
  assert.deepEqual(sortPlannerPhoneOptions(options, ['3'], '').map(o => o.value), ['3', '1', '2']);
  assert.deepEqual(sortPlannerPhoneOptions(options, ['3'], 'вик').map(o => o.value), ['3']);
  assert.deepEqual(sortPlannerPhoneOptions(options, ['3'], 'бек').map(o => o.value), ['2']);
});
