// «Мои часы» на телефоне: месяц, форматы, ячейки календаря и экран дня.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildMyHoursBreakdown,
  buildMyHoursDayDetails,
  buildMyHoursMonth,
  formatHours,
  formatMoney,
  formatMyHoursDay,
  formatMyHoursMonth,
  formatPercent,
  itemDurationHours,
  myHoursCellTone,
  myHoursNormStatus,
  myHoursRequestErrorText,
  shiftMyHoursMonth,
} from '../src/components/hours/myHoursPhone.js';

const NBSP = ' ';
const TODAY = new Date(2026, 8, 15, 12, 0);

test('месяц листается в пределах списка «Выбора месяца»', () => {
  assert.equal(shiftMyHoursMonth('2026-09', -1, TODAY), '2026-08');
  assert.equal(shiftMyHoursMonth('2026-01', -1, TODAY), '2025-12');
  assert.equal(shiftMyHoursMonth('2026-09', 1, TODAY), null, 'будущий месяц не запрашиваем');
  assert.equal(shiftMyHoursMonth('2025-10', -1, TODAY), null, 'двенадцатый прошлый месяц уже вне списка');
  assert.equal(shiftMyHoursMonth('2025-11', -1, TODAY), '2025-10');
  assert.equal(shiftMyHoursMonth('мусор', -1, TODAY), null);
  assert.equal(formatMyHoursMonth('2026-09'), 'Сентябрь 2026');
});

test('часы, деньги и проценты — по-русски и без минуса у нуля', () => {
  assert.equal(formatHours(88.08), '88,08 ч');
  assert.equal(formatHours(168), '168 ч');
  assert.equal(formatHours(-0.001), '0 ч');
  assert.equal(formatMoney(70904.4), `70${NBSP}904 ₸`);
  assert.equal(formatPercent(52.43), '52,4 %');
});

test('статус нормы — пороги настольного progressStatus', () => {
  assert.deepEqual(myHoursNormStatus(50, 0), { label: 'Норма не задана', tone: 'slate' });
  assert.equal(myHoursNormStatus(52, 168).tone, 'amber');
  assert.equal(myHoursNormStatus(85, 168).tone, 'blue');
  assert.equal(myHoursNormStatus(100, 168).label, 'Норма выполнена');
});

test('цвет ячейки — шкала настольного календаря, будущий пустой день без заливки', () => {
  assert.equal(myHoursCellTone({ hours: 8.5, isPast: true }), 'bg-green-700 text-white');
  assert.equal(myHoursCellTone({ hours: 7, isPast: true }), 'bg-green-500 text-white');
  assert.equal(myHoursCellTone({ hours: 4, isPast: true }), 'bg-green-300 text-green-900');
  assert.equal(myHoursCellTone({ hours: 2, isPast: true }), 'bg-green-100 text-green-800');
  assert.equal(myHoursCellTone({ hours: 0, isPast: true }), 'bg-slate-100 text-slate-400');
  assert.equal(myHoursCellTone({ hours: 0, isPast: false }), 'text-slate-400');
});

test('разбивка часов не повторяет итог, когда добавок нет', () => {
  assert.deepEqual(buildMyHoursBreakdown({ base: 88 }), []);
  const rows = buildMyHoursBreakdown({ base: 80, training: 1.5, offline: 0 });
  assert.deepEqual(rows.map((row) => row.key), ['base', 'training']);
});

test('длительность сбоя — минуты, часы, затем интервал через полночь', () => {
  assert.equal(itemDurationHours({ duration_minutes: 90 }), 1.5);
  assert.equal(itemDurationHours({ duration_hours: 2 }), 2);
  assert.equal(itemDurationHours({ start_time: '23:30', end_time: '00:30' }), 1);
  assert.equal(itemDurationHours(null), 0);
});

const OP = {
  daily: {
    1: { work_time: 8.5, calls: 80, efficiency: 6, talk_time: 4.1, break_time: 0.75, fines: [] },
    4: { work_time: 7.2, calls: 60, fines: [{ amount: 250, reason: 'Опоздание', comment: 'Пробки' }] },
    9: { work_time: 6 },
    21: { work_time: 3, chat_metrics: { chats_count: 40, avg_score: 4.85, avg_response_time_seconds: 95, transfer_chat_count: 2 } },
  },
  technical_issues_by_day: { 10: [{ start_time: '12:00', end_time: '12:45', reason: 'Гарнитура' }] },
  offline_activities_by_day: {},
  tez_successes_by_day: {},
};
const TRAININGS = [
  { id: 1, date: '2026-09-09', start_time: '10:00', end_time: '11:30', reason: 'Тренинг по продукту', count_in_hours: true },
  { id: 2, date: '2026-09-09', start_time: '15:00', end_time: '15:30', reason: 'Обратная связь', count_in_hours: false },
];
// Упрощённая замена computeUniqueTrainingDurationHours из App: сумма интервалов, прошедших фильтр.
const countTrainingHours = (list, predicate) => list
  .filter((item) => !predicate || predicate(item))
  .reduce((sum, item) => sum + itemDurationHours(item), 0);
const resolveDayModel = (_op, day, fallback) => (day >= 21 ? 'chat_manager' : fallback);

const month = () => buildMyHoursMonth({
  op: OP,
  month: '2026-09',
  trainings: TRAININGS,
  monthModelCode: 'operator',
  resolveDayModel,
  countTrainingHours,
  today: TODAY,
});

test('месяц: пустые места до первого числа, часы с добавками и метки дня', () => {
  const { leading, cells, markers } = month();
  assert.equal(leading, 1, '1 сентября 2026 — вторник');
  assert.equal(cells.length, 30);
  const day9 = cells[8];
  assert.equal(day9.hours, 7.5, 'зачтён только тренинг с count_in_hours');
  assert.deepEqual(day9.markers, ['training']);
  assert.deepEqual(cells[3].markers, ['fines']);
  assert.equal(cells[9].hours, 0.75);
  assert.equal(cells[14].isToday, true);
  assert.equal(cells[13].isPast, true);
  assert.equal(cells[15].isPast, false);
  assert.deepEqual(markers, ['training', 'technical', 'fines']);
  assert.equal(buildMyHoursMonth({ month: '2026-13' }).cells.length, 0);
});

test('экран дня: показатели звонков, штраф с минутами, пустой день без прочерков', () => {
  const { cells } = month();
  const day1 = buildMyHoursDayDetails({ cell: cells[0], op: OP, countTrainingHours });
  assert.equal(day1.title, '1 сентября');
  assert.equal(day1.subtitle, 'Вторник');
  assert.deepEqual(day1.metrics.map((m) => [m.label, m.value]), [
    ['Перерыв', '0,75 ч'],
    ['Звонки', '80'],
    ['Эффективность', '70,6 %'],
    ['Время в разговоре', '4,1 ч'],
  ]);
  assert.deepEqual(day1.breakdown, []);

  const day4 = buildMyHoursDayDetails({ cell: cells[3], op: OP, countTrainingHours });
  assert.equal(day4.finesTotal, 250);
  assert.equal(day4.fines[0].minutes, 5);

  const empty = buildMyHoursDayDetails({ cell: cells[19], op: OP, countTrainingHours });
  assert.deepEqual(empty.metrics, []);
});

test('экран дня: тренинги с незачтённым, тех. сбой и чат-модель', () => {
  const { cells } = month();
  const day9 = buildMyHoursDayDetails({ cell: cells[8], op: OP, countTrainingHours });
  assert.equal(day9.trainingCountedHours, 1.5);
  assert.deepEqual(day9.trainings.map((t) => [t.time, t.hours, t.counted]), [
    ['10:00 — 11:30', 1.5, true],
    ['15:00 — 15:30', 0.5, false],
  ]);
  assert.deepEqual(day9.breakdown.map((row) => row.key), ['base', 'training']);

  const day10 = buildMyHoursDayDetails({ cell: cells[9], op: OP, countTrainingHours });
  assert.equal(day10.technicalHours, 0.75);
  assert.equal(day10.technical[0].reason, 'Гарнитура');

  const day21 = buildMyHoursDayDetails({ cell: cells[20], op: OP, countTrainingHours });
  assert.deepEqual(day21.metrics.map((m) => m.value), ['—', '40', '4,85', '1,58 мин', '2']);
  assert.equal(formatMyHoursDay('2026-09-20').subtitle, 'Воскресенье');
});

test('экран дня: тренинги по времени начала, а не в порядке ответа сервера', () => {
  const { cells } = month();
  const reversed = { ...cells[8], trainings: [...cells[8].trainings].reverse() };
  const details = buildMyHoursDayDetails({ cell: reversed, op: OP, countTrainingHours });
  assert.deepEqual(details.trainings.map((t) => t.time), ['10:00 — 11:30', '15:00 — 15:30']);
});

test('ошибка запроса: русский ответ сервера как есть, английский — общей фразой', () => {
  assert.equal(myHoursRequestErrorText({ response: { data: { error: 'Супервайзер не назначен' } } }), 'Супервайзер не назначен');
  assert.equal(
    myHoursRequestErrorText({ response: { data: { error: 'Failed to send request: Unauthorized' } } }),
    'Не удалось отправить запрос. Попробуйте ещё раз.',
  );
  assert.equal(myHoursRequestErrorText(new Error('Network Error')), 'Не удалось отправить запрос. Попробуйте ещё раз.');
});
