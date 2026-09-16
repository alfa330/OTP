// «Учет часов» на телефоне: месяц, форматы, итоги строки и подвала, ячейки
// календаря и правила штрафов с бонусами.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildHoursPhoneDayFields,
  buildHoursPhoneMonth,
  buildHoursPhoneOperatorTotals,
  buildHoursPhoneSectionTotals,
  formatHoursPhoneCompactMoney,
  formatHoursPhoneDay,
  formatHoursPhoneHours,
  formatHoursPhoneMoney,
  formatHoursPhoneMonth,
  formatHoursPhonePercent,
  hoursPhoneBonusHasQuantity,
  hoursPhoneBonusQuantityLabel,
  hoursPhoneDirectionsLabel,
  hoursPhoneFineAmount,
  hoursPhoneFineHint,
  hoursPhoneFineIsAuto,
  hoursPhoneMonthOptions,
  hoursPhoneOperatorRows,
  hoursPhoneOperatorValue,
  hoursPhoneProductionTone,
  hoursPhoneTone,
  shiftHoursPhoneMonth,
} from '../src/components/hours/hoursAccountingPhone.js';

const NBSP = '\u00A0'; // неразрывный пробел из Intl.NumberFormat('ru-RU')
const TODAY = new Date(2026, 8, 16, 12, 0);

/* Длительности считает раздел (App.jsx) — здесь их подменяют простые правила:
   тренинг/сбой/офлайн меряются полем hours, «без телефона» — минутами. */
const helpers = {
  trainingHours: (items, predicate) => (Array.isArray(items) ? items : [])
    .filter((item) => (typeof predicate === 'function' ? predicate(item) : true))
    .reduce((sum, item) => sum + Number(item.hours || 0), 0),
  technicalHours: (item) => Number(item?.hours || 0),
  offlineHours: (item) => Number(item?.hours || 0),
  noPhoneHours: (entry) => Number(entry?.no_phone_minutes || 0) / 60,
};

test('месяц листается на два года назад и на месяц вперёд', () => {
  assert.equal(shiftHoursPhoneMonth('2026-09', -1, TODAY), '2026-08');
  assert.equal(shiftHoursPhoneMonth('2026-09', 1, TODAY), '2026-10', 'месяц вперёд нужен, чтобы заранее проставить норму');
  assert.equal(shiftHoursPhoneMonth('2026-10', 1, TODAY), null, 'дальше следующего месяца не ходим');
  assert.equal(shiftHoursPhoneMonth('2024-11', -1, TODAY), '2024-10', 'двадцать третий прошлый месяц ещё в списке');
  assert.equal(shiftHoursPhoneMonth('2024-10', -1, TODAY), null, 'за краем списка месяца нет');
  assert.equal(shiftHoursPhoneMonth('мусор', -1, TODAY), null);
  assert.equal(formatHoursPhoneMonth('2026-09'), 'Сентябрь 2026');
});

test('в колесе месяцев всегда есть выбранный месяц, даже вне окна', () => {
  const options = hoursPhoneMonthOptions('2026-09', TODAY);
  assert.equal(options[0].value, '2026-10');
  assert.equal(options.at(-1).value, '2024-10', 'двадцать три месяца назад от текущего');
  assert.equal(options.length, 25);
  assert.ok(options.some((option) => option.value === '2026-09'));

  const far = hoursPhoneMonthOptions('2019-03', TODAY);
  assert.equal(far[0].value, '2019-03', 'месяц из ссылки встаёт первым, а не подменяется чужим');
  assert.equal(far[0].label, 'Март 2019');
});

test('числа, часы, проценты и деньги — по-русски и без минуса у нуля', () => {
  assert.equal(formatHoursPhoneHours(128), '128 ч');
  assert.equal(formatHoursPhoneHours(7.254), '7,25 ч');
  assert.equal(formatHoursPhoneHours(-0.001), '0 ч');
  assert.equal(formatHoursPhonePercent(54.93), '54,9 %');
  assert.equal(formatHoursPhonePercent(null), '—');
  assert.equal(formatHoursPhoneMoney(10000), `10${NBSP}000 ₸`);
  assert.equal(formatHoursPhoneDay('2026-09', 10), '10 сентября, четверг');
});

test('деньги в ячейке дня сжимаются до трёх знаков', () => {
  assert.equal(formatHoursPhoneCompactMoney(0), '', 'ноль в ячейке не рисуем вовсе');
  assert.equal(formatHoursPhoneCompactMoney(500), '500');
  assert.equal(formatHoursPhoneCompactMoney(5000), '5к');
  assert.equal(formatHoursPhoneCompactMoney(10000), '10к');
  assert.equal(formatHoursPhoneCompactMoney(12500), '12,5к');
  assert.equal(formatHoursPhoneCompactMoney(-5000), '-5к');
});

const operator = {
  operator_id: 7,
  name: 'Оператор',
  rate: 1,
  norm_hours: 160,
  aggregates: { regular_hours: 100, total_calls: 400, total_efficiency_hours: 50, total_chats: 30 },
  daily: {
    1: { work_time: 8, break_time: 1, calls: 40, efficiency: 4, fines: [{ amount: 500, reason: 'Опоздание' }] },
    2: { work_time: 8, break_time: 0.5, calls: 60, bonuses: [{ amount: 5000, type: 'Приведи друга' }] },
    3: { work_time: 4, no_phone_minutes: 30 },
  },
};
const trainingsByDay = { 1: [{ hours: 2, count_in_hours: true }], 3: [{ hours: 1, count_in_hours: false }] };
const technicalByDay = { 2: [{ hours: 0.5 }] };
const offlineByDay = { 2: [{ hours: 1.5 }] };

test('итоги оператора повторяют формулы настольной строки', () => {
  const totals = buildHoursPhoneOperatorTotals({ op: operator, month: '2026-09', trainingsByDay, technicalByDay, offlineByDay, helpers });
  assert.equal(totals.regular, 100);
  assert.equal(totals.trainingAll, 3);
  assert.equal(totals.trainingCounted, 2, 'незасчитанный тренинг в часы не идёт');
  assert.equal(totals.trainingNotCounted, 1);
  assert.equal(totals.technical, 0.5);
  assert.equal(totals.offline, 1.5);
  // база + зачтённые тренинги + техсбои + офлайн — ровно displayedTotal на компьютере
  assert.equal(totals.displayedTotal, 104);
  assert.equal(totals.production, -56);
  assert.equal(Math.round(totals.normPercent * 10) / 10, 65);
  assert.equal(totals.breakTime, 1.5);
  assert.equal(totals.fines, 500);
  assert.equal(totals.bonuses, 5000);
  assert.equal(totals.noPhone, 0.5);
  assert.equal(totals.calls, 400, 'агрегат месяца сильнее суммы по дням');
  assert.equal(totals.occ, 50);
});

test('КВЗ переведённого считается по часам своей модели', () => {
  const moved = {
    ...operator,
    aggregates: { regular_hours: 20, total_calls: 100 },
    daily: { 1: { work_time: 5, calls: 50 }, 2: { work_time: 5, calls: 50 }, 3: { work_time: 10 } },
    group_segments: [
      { start_day: 1, end_day: 2, calculation_model_code: 'operator', group_name: 'Линия', is_current: true },
      { start_day: 3, end_day: 30, calculation_model_code: 'chat_manager', group_name: 'Чаты', is_current: false },
    ],
  };
  const line = buildHoursPhoneOperatorTotals({ op: moved, month: '2026-09', isChatModel: false, helpers });
  assert.equal(line.callHours, 10, 'в знаменатель идут только дни своей модели');
  const chat = buildHoursPhoneOperatorTotals({ op: moved, month: '2026-09', isChatModel: true, helpers });
  assert.equal(chat.callHours, 10);
});

test('число в строке оператора — первая итоговая колонка вкладки', () => {
  const totals = buildHoursPhoneOperatorTotals({ op: operator, month: '2026-09', trainingsByDay, technicalByDay, offlineByDay, helpers });
  assert.deepEqual(hoursPhoneOperatorValue({ tab: 'work_time', totals }), { text: '104 ч', caption: 'Итого часов' });
  assert.equal(hoursPhoneOperatorValue({ tab: 'trainings', totals }).text, '3 ч');
  assert.equal(hoursPhoneOperatorValue({ tab: 'fines', totals }).text, `500 ₸`);
  assert.equal(hoursPhoneOperatorValue({ tab: 'no_phone', totals }).text, '0,5 ч');
  // КВЗ: 400 обращений на 100 базовых часов
  assert.deepEqual(hoursPhoneOperatorValue({ tab: 'calls', totals }), { text: '4', caption: 'КВЗ, звонков в час' });
  assert.equal(hoursPhoneOperatorValue({ tab: 'calls', totals, isTezOpContext: true }).text, '400');
  assert.equal(hoursPhoneOperatorValue({ tab: 'calls', totals, isChatModel: true }).caption, 'Чатов в час');
  assert.equal(hoursPhoneOperatorValue({ tab: 'avg_score', totals, chatAverage: null }).text, '—');
  assert.equal(hoursPhoneOperatorValue({ tab: 'response_time', totals, responseAverage: 42.4 }).text, '42 с');
});

test('строки итогов оператора: только те, что есть в колонках вкладки', () => {
  const totals = buildHoursPhoneOperatorTotals({ op: operator, month: '2026-09', trainingsByDay, technicalByDay, offlineByDay, helpers });
  const work = hoursPhoneOperatorRows({ tab: 'work_time', totals });
  assert.deepEqual(work.map((row) => row.key), ['regular', 'norm', 'production']);
  assert.equal(work[2].valueClassName, 'text-rose-600', 'недовыработка красная, как плашка на компьютере');
  assert.deepEqual(hoursPhoneOperatorRows({ tab: 'fines', totals }), [], 'у денег второй колонки нет');
  const trainings = hoursPhoneOperatorRows({ tab: 'trainings', totals });
  assert.deepEqual(trainings.map((row) => row.key), ['counted', 'not-counted']);
  const successes = hoursPhoneOperatorRows({ tab: 'tez_successes', totals: { ...totals, successes: 30 }, plan: 40 });
  assert.deepEqual(successes.map((row) => row.value), ['40', '75 %', '0,29']);
});

test('итог раздела повторяет подвал таблицы', () => {
  const footer = {
    sumDisplayedTotal: 676, sumRegular: 676, sumNorm: 1232, sumProd: -556,
    sumRate: 7, hasRateRows: true, sumBreakTime: 46.5, sumEff: 338, avgOcc: 50,
    trainingsTotal: 3, technicalIssuesTotal: 1, offlineActivitiesTotal: 2, noPhoneTotal: 4,
    sumFines: 1500, sumBonuses: 5000, sumCalls: 4056, sumChats: 12, sumDialTime: 5, sumTalkTime: 6,
    sumTezSuccesses: 120, sumTezPlan: 150, hasTezPlanRows: true,
  };
  const work = buildHoursPhoneSectionTotals({ tab: 'work_time', footer });
  assert.equal(work.value, '676 ч');
  assert.equal(work.caption, 'Итого часов');
  assert.deepEqual(work.rows.map((row) => row.key), ['regular', 'norm-pct', 'production', 'fte', 'norm']);
  assert.equal(work.rows.at(-2).value, '7 FTE');

  // КВЗ по отделу настольный подвал не считает — не выдумываем его и здесь.
  assert.equal(buildHoursPhoneSectionTotals({ tab: 'calls', footer }).value, '—');
  assert.equal(buildHoursPhoneSectionTotals({ tab: 'calls', footer, isTezOpContext: true }).value, `4${NBSP}056`);
  assert.equal(buildHoursPhoneSectionTotals({ tab: 'fines', footer }).value, `1${NBSP}500 ₸`);
  const successes = buildHoursPhoneSectionTotals({ tab: 'tez_successes', footer });
  assert.equal(successes.value, '120');
  assert.equal(successes.rows[1].value, '80 %');
});

test('календарь месяца: значение дня, метки и чужой день под замком', () => {
  const model = buildHoursPhoneMonth({
    op: { ...operator, group_segments: [
      { start_day: 1, end_day: 2, group_name: 'Линия', is_current: true },
      { start_day: 3, end_day: 30, group_name: 'Чаты', is_current: false },
    ] },
    month: '2026-09',
    days: [1, 2, 3, 4],
    tab: 'work_time',
    monthRelation: 0,
    todayDay: 2,
    trainingsByDay,
    technicalByDay,
    offlineByDay,
    selectedGroupId: '5',
    scales: { work: 10 },
    helpers,
  });
  assert.equal(model.leading, 1, 'сентябрь 2026 начинается со вторника');
  const [d1, d2, d3, d4] = model.cells;
  assert.equal(d1.text, '10', '8 базовых + 2 зачтённых часа тренинга');
  assert.deepEqual(d1.markers, ['training', 'fines']);
  assert.equal(d2.text, '10', '8 базовых + 0,5 техсбоя + 1,5 офлайна');
  assert.ok(d2.isToday);
  assert.deepEqual(d2.markers, ['technical', 'offline']);
  assert.ok(d3.locked, 'день чужой группы — замок');
  assert.equal(d3.lockedGroup, 'Чаты');
  assert.deepEqual(d3.markers, [], 'у чужого дня меток не показываем');
  assert.ok(d4.locked);
  assert.deepEqual(model.markers, ['training', 'technical', 'offline', 'fines']);
});

test('будущий пустой день не заливается, прошедший — серый', () => {
  const model = buildHoursPhoneMonth({
    op: { operator_id: 1, daily: {} }, month: '2026-09', days: [1, 2, 3],
    tab: 'work_time', monthRelation: 0, todayDay: 2, scales: { work: 8 }, helpers,
  });
  assert.match(model.cells[0].tone, /bg-slate-100/);
  assert.match(model.cells[2].tone, /text-slate-300/);
  assert.equal(model.cells[2].text, '');
});

test('время ответа красится наоборот: меньше — лучше', () => {
  const op = { operator_id: 1, daily: {
    1: { chat_metrics: { avg_response_time_seconds: 30 } },
    2: { chat_metrics: { avg_response_time_seconds: 120 } },
    3: { chat_metrics: { avg_response_time_seconds: 400 } },
  } };
  const model = buildHoursPhoneMonth({ op, month: '2026-09', days: [1, 2, 3], tab: 'response_time', helpers });
  assert.match(model.cells[0].tone, /green/);
  assert.match(model.cells[1].tone, /amber/);
  assert.match(model.cells[2].tone, /rose/);
});

test('эффективность в ячейке — процент от рабочего времени', () => {
  const op = { operator_id: 1, daily: { 1: { work_time: 8, efficiency: 6 }, 2: { work_time: 0, efficiency: 3 } } };
  const model = buildHoursPhoneMonth({ op, month: '2026-09', days: [1, 2], tab: 'efficiency', helpers });
  assert.equal(model.cells[0].text, '75%');
  assert.equal(model.cells[1].text, '', 'без рабочего времени процента нет — как прочерк на компьютере');
});

test('заливка ячейки — четыре ступени доли от максимума', () => {
  assert.match(hoursPhoneTone(2, 10, 'green'), /bg-green-100/);
  assert.match(hoursPhoneTone(4, 10, 'green'), /bg-green-300/);
  assert.match(hoursPhoneTone(7, 10, 'green'), /bg-green-500/);
  assert.match(hoursPhoneTone(10, 10, 'green'), /bg-green-700/);
  assert.match(hoursPhoneTone(5, 0, 'green'), /bg-green-100/, 'без масштаба не красим тёмным');
});

test('цвет выработки — пороги настольного productionColorClass', () => {
  assert.equal(hoursPhoneProductionTone(-1, 160), 'text-rose-600');
  assert.equal(hoursPhoneProductionTone(5, 160), 'text-amber-600');
  assert.equal(hoursPhoneProductionTone(50, 160), 'text-emerald-700');
  assert.equal(hoursPhoneProductionTone(1, 0), 'text-slate-500');
});

test('суммы штрафов считаются теми же правилами, что при сохранении', () => {
  assert.equal(hoursPhoneFineAmount({ reason: 'Опоздание', minutes: 12, amount: 1 }), 600);
  assert.equal(hoursPhoneFineAmount({ reason: 'Не выход', amount: 1 }), 10000);
  assert.equal(hoursPhoneFineAmount({ reason: 'Прокси карта', amount: 1 }), 5000);
  assert.equal(hoursPhoneFineAmount({ reason: 'Другое', amount: 777 }), 777);
  assert.ok(hoursPhoneFineIsAuto({ reason: 'Опоздание' }));
  assert.ok(!hoursPhoneFineIsAuto({ reason: 'Другое' }));
  assert.match(hoursPhoneFineHint({ reason: 'Опоздание' }), /минуты × 50/);
  assert.equal(hoursPhoneFineHint({ reason: 'Другое' }), '');
});

test('бонус: «Обучение» меряется часами, остальные — штуками', () => {
  assert.equal(hoursPhoneBonusQuantityLabel('Обучение'), 'Часы');
  assert.equal(hoursPhoneBonusQuantityLabel('Съемки'), 'Кол-во');
  assert.ok(hoursPhoneBonusHasQuantity('Приведи друга'));
  assert.ok(!hoursPhoneBonusHasQuantity(''));
});

test('набор полей дня зависит от модели расчёта', () => {
  const line = buildHoursPhoneDayFields({ isChatModel: false }).map((field) => field.key);
  assert.deepEqual(line, ['work_time', 'break_time', 'talk_time', 'calls', 'efficiency']);
  const chat = buildHoursPhoneDayFields({ isChatModel: true }).map((field) => field.key);
  assert.deepEqual(chat, ['work_time', 'break_time', 'calls'], 'у чата нет разговора и эффективности');
});

test('подпись отбора направлений', () => {
  assert.equal(hoursPhoneDirectionsLabel(['all']), 'Все');
  assert.equal(hoursPhoneDirectionsLabel([]), 'Ничего');
  assert.equal(hoursPhoneDirectionsLabel(['ОП линия']), 'ОП линия');
  assert.equal(hoursPhoneDirectionsLabel(['ОП линия', 'Чаты']), 'Выбрано 2');
});
