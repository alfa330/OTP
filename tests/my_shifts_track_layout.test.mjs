import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    buildMyShiftsTrackLayout,
    describeMyShiftsTrack,
    formatTrackDuration,
    formatTrackTime,
    pickTrackSegment,
    resolveTrackTap,
} from '../src/components/schedule/myShiftsTrackLayout.js';

/*
 * «Мои смены»: три ленты дня (решение владельца, 07.10.2026) — смена, статусы
 * ВНУТРИ смены и несоответствия графику.
 *
 * День собирает getMyStatusTrackForDate внутри огромного App.jsx. Тест вынимает
 * из исходника её саму вместе со сборкой оператора (myTimelineOperator),
 * расчётом соответствия и всеми помощниками и исполняет на своих данных:
 * проверяется не пересказ проводки, а она сама — тот же код, что рисует экран
 * оператора. Раскладка и слова (myShiftsTrackLayout.js) подключаются обычным
 * импортом, компонент рендерится через react-dom/server.
 *
 * Что стережётся:
 *   — вторая лента не выходит за смену, работа до и после отмечена отдельно;
 *   — опоздание красным, и статус под ним не рисуется и не считается;
 *   — красное в третьей ленте в сумме равно «100% минус совпадение»;
 *   — смена, заходящая в сегодняшние сутки, считается по момент последних
 *     статусов; дни, оставшиеся во вчера, — целиком, как у руководителя;
 *   — опоздание и ранний уход принадлежат дню, в который смена началась.
 *
 * Чего здесь нет: перерывы во всех сменах заданы явно — правила их расстановки
 * по направлению (computeBreaksForShiftMinutes) проверяются своими тестами.
 */

const APP = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');

function slice(source, start, end) {
    const from = source.indexOf(start);
    assert.notEqual(from, -1, `нет начала: ${start}`);
    // Якорь обязан быть единственным: иначе после чужой правки тест молча
    // вырежет не тот кусок и будет зеленеть на постороннем коде.
    assert.equal(source.indexOf(start, from + 1), -1, `начало встречается дважды: ${start}`);
    const to = source.indexOf(end, from + start.length);
    assert.notEqual(to, -1, `нет конца: ${end}`);
    return source.slice(from, to);
}

const PIECES = [
    // время и даты: timeToMinutes, minutesToTime, todayDateStr, parseDateStr, addDays
    slice(APP, 'const cpad = (n) => String(n).padStart(2, "0");', '// Прошедшие перерывы не пересчитываются'),
    // интервалы, справочник статусов с цветами, расчёт соответствия
    slice(APP, 'const mergeIntervals = (arr) => {', 'const plannerStatusFormatDuration = (seconds) => {'),
    slice(APP, 'const plannerStatusDayKey = (value) => {', 'const plannerStatusFormatDayLabel = (dayKey) => {'),
    // статусы суток и «сквозные» минуты ночной смены
    slice(APP, 'const plannerStatusImportedSegmentToDayMinutes = (seg, dateKey) => {', 'const plannerNoPhoneShiftMetricsForTimeline = '),
    slice(APP, 'const plannerTimelineDaysWithLiveTail = (op) => {', 'const buildPlannerStatusAnalysisFromOperators = (operatorsList = []) => {'),
    // обоснованные активности: тренинг, тех. сбой, офлайн-работа
    slice(APP, 'const plannerStatusMatchActivityInterval = (seg) => {', 'const createPlannerStatusMatchAccumulator = () => ({'),
    // смены и перерывы внутри суток
    slice(APP, 'const getShiftPartsForDate = (op, dateStr) => {', 'const totalsPerHourForDate = (dateStr) => {'),
    slice(APP, 'const getBreakPartsForPart = (op, p, dateStr) => {', '// Ручные перерывы СВ «тронуты»'),
];
// Оператор раздела: смены и статусы из запроса периода, поверх — живой срез дня и хвост.
const OPERATOR = slice(APP, 'const myTimelineOperator = useMemo(() => {', '/* Фактические статусы под ленту смен');
const BUILDER = slice(APP, 'const getMyStatusTrackForDate = useCallback((dateKey) => {', 'const myCurrentDayStatusTrack = useMemo(');

/* Экран оператора: env — то, что в App.jsx лежит в состоянии раздела
   (myScheduleData — ответ /api/work_schedules/my, myStatusTrackLive — живой
   срез /status_track, operatorTodayKey — сегодняшняя дата). */
function makeScreen(env) {
    const factory = new Function('buildMyShiftsTrackLayout', 'formatTrackTime', 'env', `
        const useCallback = (fn) => fn;
        const useMemo = (fn) => fn();
        const minutesInDay = 24 * 60;
        const normalizePlannerShiftType = (value) => String(value || 'regular');
        const computeBreaksForShiftMinutes = () => [];
        const getPlannerBreakRuleRangesForDirection = () => [];
        ${PIECES.join('\n')}
        const { myScheduleData, plannerTrainingsByOperator, myStatusTrackLive, operatorTodayKey } = env;
        ${OPERATOR}
        ${BUILDER}
        return { getMyStatusTrackForDate, plannerComputeShiftStatusMatchMetrics, buildImportedStatusBarsForDay };
    `);
    return factory(buildMyShiftsTrackLayout, formatTrackTime, {
        plannerTrainingsByOperator: {},
        myStatusTrackLive: null,
        ...env,
    });
}

const PREV = '2026-10-05';
const DAY = '2026-10-06';
const NEXT = '2026-10-07';
const LATER = '2026-10-09';
const min = (hhmm) => { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m; };

const WORK = new Set(['готов', 'занят', 'занята', 'перезвон', 'online', 'holiday']);
const BREAK = new Set(['перерыв', 'break']);
const LABELS = {
    готов: 'Готов', занят: 'Занят', занята: 'Занята', перезвон: 'Перезвон', перерыв: 'Перерыв',
    выключен: 'Выключен', 'без телефона': 'Без телефона', тренинг: 'Тренинг',
};

function nextDay(day) {
    const d = new Date(`${day}T00:00:00`);
    d.setDate(d.getDate() + 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
const stamp = (day, time) => {
    if (time === '24:00') return `${nextDay(day)}T00:00:00`;
    return `${day}T${time.length === 5 ? `${time}:00` : time}`;
};
/* Отрезок статуса в том виде, в каком его отдаёт сервер (признаки уже посчитаны
   по модели расчёта оператора). Время — 'ЧЧ:ММ' или 'ЧЧ:ММ:СС'. */
const seg = (day, from, to, key, extra = {}) => ({
    statusDate: day,
    start: stamp(day, from),
    end: stamp(day, to),
    durationSec: 0,
    stateName: LABELS[key] || key,
    stateKey: key,
    stateNote: '',
    isWork: WORK.has(key),
    isBreak: BREAK.has(key),
    isTraining: key === 'тренинг',
    isLateStart: WORK.has(key),
    isTechnicalReason: false,
    isLateExcused: key === 'тренинг',
    isNoPhone: key === 'без телефона',
    ...extra,
});
/* Цепочка статусов одного дня: [['00:00', 'выключен'], ['09:17', 'готов'], …, ['24:00']]. */
const chain = (day, points) => points.slice(0, -1).map(([from, key], index) => seg(day, from, points[index + 1][0], key));

const BREAKS = [{ start: min('11:00'), end: min('11:15') }, { start: min('13:00'), end: min('13:45') }, { start: min('16:00'), end: min('16:15') }];
const shift = (start, end, breaks = BREAKS) => ({ start, end, shift_type: 'regular', breaks });
const NIGHT = { start: '21:00', end: '09:00', shift_type: 'regular', breaks: [] };
const operator = (shifts, days, extra = {}) => ({
    id: 4,
    shifts,
    importedStatusTimelineDays: days,
    technicalIssueTimelineDays: {},
    offlineActivityTimelineDays: {},
    ...extra,
});

const sum = (list, pick) => list.reduce((acc, item) => acc + pick(item), 0);
const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-6, `${message}: ${actual} ≠ ${expected}`);

// Плохой день: опоздал на 17 минут, обед взял на 40 минут раньше графика, четыре
// минуты «без телефона», затянул перерыв и ушёл за 20 минут до конца.
const BAD_DAY = chain(DAY, [
    ['00:00', 'выключен'], ['09:17', 'готов'], ['10:10', 'занят'], ['10:20', 'готов'], ['11:00', 'перерыв'], ['11:15', 'готов'],
    ['12:20', 'перерыв'], ['13:05', 'готов'], ['13:30', 'занят'], ['13:40', 'готов'], ['14:30', 'без телефона'], ['14:34', 'готов'],
    ['16:00', 'перерыв'], ['16:25', 'готов'], ['17:40', 'выключен'], ['24:00'],
]);
// Хороший день: вошёл на 8 минут раньше, ушёл на 14 позже, перерывы по графику.
const GOOD_DAY = chain(DAY, [
    ['00:00', 'выключен'], ['08:52', 'готов'], ['11:00', 'перерыв'], ['11:15', 'готов'], ['13:00', 'перерыв'], ['13:45', 'занят'],
    ['14:00', 'готов'], ['16:00', 'перерыв'], ['16:15', 'готов'], ['18:14', 'выключен'], ['24:00'],
]);
const DAY_SHIFT = { [DAY]: [shift('09:00', '18:00')] };
const PLAIN_SHIFT = { [DAY]: [shift('09:00', '18:00', [])] };

/* Карточка дня DAY. По умолчанию «сегодня» далеко впереди — день давно прошёл. */
const cardOf = (segments, shifts = DAY_SHIFT, { today = LATER, days = null, ...env } = {}) => makeScreen({
    myScheduleData: operator(shifts, days || { [DAY]: segments }),
    operatorTodayKey: today,
    ...env,
}).getMyStatusTrackForDate(DAY);

/* ── Вторая лента: статусы внутри смены ───────────────────────────────────── */

test('вторая лента не выходит за смену: статусы только внутри, сутки «Выключен» не рисуются', () => {
    const { spans, totals } = cardOf(GOOD_DAY).layout;
    assert.equal(spans.length, 1);
    const [span] = spans;
    assert.equal(span.startMin, min('09:00'));
    assert.equal(span.endMin, min('18:00'));
    near(span.left, (540 / 1440) * 100, 'начало полосы — начало смены');
    near(span.width, (540 / 1440) * 100, 'ширина полосы — длина смены');
    assert.ok(span.bars.length > 0);
    span.bars.forEach((bar) => {
        assert.ok(bar.startMin >= span.startMin && bar.endMin <= span.endMin, `${bar.tooltip} вышел за смену`);
        assert.ok(bar.left >= -1e-6 && bar.left + bar.width <= 100 + 1e-6, `${bar.tooltip} вышел за полосу`);
    });
    // «Выключен» был только до и после смены — в подписях под полосой его нет.
    assert.deepEqual(totals.map((item) => item.label), ['Готов', 'Перерыв', 'Занят']);
    near(sum(totals, (item) => item.minutes), 540, 'подписи считают только минуты смены');
});

test('работа до начала и после конца смены отмечена отдельно и подписана', () => {
    const track = cardOf(GOOD_DAY);
    const { layout } = track;
    assert.deepEqual(layout.outside.map((item) => [item.kind, item.startMin, item.endMin]), [
        ['before', min('08:52'), min('09:00')],
        ['after', min('18:00'), min('18:14')],
    ]);
    assert.equal(layout.outside[0].tooltip, 'Работа до смены • 08:52 — 09:00 • 8 мин');
    assert.equal(layout.outside[1].tooltip, 'Работа после смены • 18:00 — 18:14 • 14 мин');
    near(layout.beforeMin, 8, 'до смены');
    near(layout.afterMin, 14, 'после смены');
    assert.equal(layout.offMin, 0);
    // Метки лежат на оси суток, снаружи полосы смены.
    const [span] = layout.spans;
    assert.ok(layout.outside[0].left + layout.outside[0].width <= span.left + 1e-6);
    near(layout.outside[1].left, span.left + span.width, 'метка «после» начинается у конца смены');
    // Под лентами — те же слова и те же минуты.
    assert.deepEqual(describeMyShiftsTrack(track).outside.map((item) => item.text), ['до смены 8 мин', 'после смены 14 мин']);
});

test('порог метки — полминуты: вход за минуту до смены отмечен, за двадцать секунд — нет', () => {
    const enteredAt = (time) => {
        const day = chain(DAY, [['00:00', 'выключен'], [time, 'готов'], ['18:00', 'выключен'], ['24:00']]);
        return cardOf(day, PLAIN_SHIFT);
    };
    const minuteEarly = enteredAt('08:59:00');
    assert.deepEqual(minuteEarly.layout.outside.map((item) => item.tooltip), ['Работа до смены • 08:59 — 09:00 • 1 мин']);
    assert.deepEqual(describeMyShiftsTrack(minuteEarly).outside.map((item) => item.text), ['до смены 1 мин']);
    const secondsEarly = enteredAt('08:59:40');
    assert.deepEqual(secondsEarly.layout.outside, []);
    assert.equal(secondsEarly.layout.beforeMin, 0);
    assert.deepEqual(describeMyShiftsTrack(secondsEarly).outside, []);
});

test('опоздание — красным в начале смены; статус под ним не рисуется и в подписи не считается', () => {
    const track = cardOf(BAD_DAY);
    const [span] = track.layout.spans;
    assert.equal(span.late.length, 1);
    assert.equal(span.late[0].startMin, min('09:00'));
    assert.equal(span.late[0].endMin, min('09:17'));
    assert.equal(span.late[0].tooltip, 'Опоздание • 09:00 — 09:17 • 17 мин');
    near(span.late[0].left, 0, 'опоздание стоит у левого края полосы');
    span.bars.forEach((bar) => assert.ok(bar.startMin >= min('09:17'), `${bar.tooltip} лежит под опозданием`));
    assert.equal(track.metrics.lateTotalMin, 17);
    assert.equal(track.metrics.earlyLeaveTotalMin, 20);
    // «Выключен» в подписи — только двадцать минут раннего ухода, без 17 минут опоздания.
    near(track.layout.totals.find((item) => item.label === 'Выключен').minutes, 20, 'Выключен внутри смены');
    // Подсказка статуса, обрезанного сменой, — про сам статус целиком.
    assert.equal(span.bars[span.bars.length - 1].tooltip, 'Выключен • 17:40 — 00:00');
    assert.deepEqual(describeMyShiftsTrack(track).problems.map((item) => item.text), ['опоздание 17 мин', 'ранний уход 20 мин']);
});

test('вход через двадцать секунд после начала — не опоздание: ни красного, ни слова', () => {
    const day = chain(DAY, [['00:00', 'выключен'], ['09:00:20', 'готов'], ['18:00', 'выключен'], ['24:00']]);
    const track = cardOf(day, PLAIN_SHIFT);
    assert.deepEqual(track.layout.spans[0].late, []);
    assert.deepEqual(describeMyShiftsTrack(track).problems, []);
    // Эти двадцать секунд остаются на ленте обычным статусом.
    assert.equal(track.layout.spans[0].bars[0].label, 'Выключен');
});

test('опоздание считается от конца обоснованного отсутствия, а не от начала смены', () => {
    // С 09:00 тренинг (статусом), в 09:30 он кончился, на линию человек вышел в 09:45.
    const day = chain(DAY, [['00:00', 'выключен'], ['09:00', 'тренинг'], ['09:30', 'выключен'], ['09:45', 'готов'], ['18:00', 'выключен'], ['24:00']]);
    const track = cardOf(day, PLAIN_SHIFT);
    const [span] = track.layout.spans;
    assert.deepEqual(span.late.map((item) => [item.startMin, item.endMin, item.tooltip]), [
        [min('09:30'), min('09:45'), 'Опоздание • 09:30 — 09:45 • 15 мин'],
    ]);
    assert.equal(track.metrics.lateTotalMin, 15);
    // Тренинг перед опозданием остаётся на полосе своим цветом.
    assert.deepEqual(span.bars.slice(0, 2).map((bar) => [bar.label, bar.startMin, bar.endMin]), [
        ['Тренинг', min('09:00'), min('09:30')],
        ['Готов', min('09:45'), min('18:00')],
    ]);
});

test('статусы внахлёст рисуются и считаются один раз', () => {
    // Свежий срез лёг поверх отрезка выгрузки: «Готов» на всю смену и ещё два куска внутри.
    const day = [
        ...chain(DAY, [['09:00', 'готов'], ['18:00']]),
        seg(DAY, '09:30', '10:30', 'занят'),
        seg(DAY, '10:00', '11:00', 'готов'),
    ];
    const { layout } = cardOf(day, PLAIN_SHIFT);
    assert.deepEqual(layout.totals.map((item) => [item.label, item.minutes]), [['Готов', 540]]);
    assert.equal(layout.spans[0].bars.length, 1);
});

/* ── Третья лента: несоответствия ─────────────────────────────────────────── */

test('третья лента — ровно те минуты, которых не хватило до 100%', () => {
    const track = cardOf(BAD_DAY);
    const [span] = track.layout.spans;
    assert.deepEqual(span.mismatches.map((item) => item.tooltip), [
        'Выключен вместо работы • 09:00 — 09:17 • 17 мин',
        'Перерыв вместо работы • 12:20 — 13:00 • 40 мин',
        'Готов, Занят вместо перерыва • 13:05 — 13:45 • 40 мин',
        'Без телефона вместо работы • 14:30 — 14:34 • 4 мин',
        'Перерыв вместо работы • 16:15 — 16:25 • 10 мин',
        'Выключен вместо работы • 17:40 — 18:00 • 20 мин',
    ]);
    near(track.layout.mismatchMin, 131, 'сумма несоответствий');
    near(sum(span.mismatches, (item) => item.endMin - item.startMin), track.layout.mismatchMin, 'сумма отрезков');
    near(track.metrics.totalScheduledMin - track.metrics.matchedTotalMin, track.layout.mismatchMin, 'остаток от совпавшего');
    assert.equal(Math.round(track.metrics.compliancePct), 76);
    span.mismatches.forEach((item) => {
        assert.ok(item.left >= -1e-6 && item.left + item.width <= 100 + 1e-6, `${item.tooltip} вышел за полосу`);
    });
    assert.equal(describeMyShiftsTrack(track).mismatchLabel, '2ч 11м');
    assert.equal(describeMyShiftsTrack(track).compliance, '76%');
});

test('день по графику: третья лента пустая, совпадение 100%', () => {
    const day = chain(DAY, [
        ['00:00', 'выключен'], ['09:00', 'готов'], ['10:00', 'тренинг'], ['11:00', 'перерыв'], ['11:15', 'готов'], ['13:00', 'перерыв'],
        ['13:45', 'готов'], ['16:00', 'перерыв'], ['16:15', 'готов'], ['18:00', 'выключен'], ['24:00'],
    ]);
    const track = cardOf(day);
    assert.equal(track.layout.mismatchMin, 0);
    assert.deepEqual(track.layout.spans[0].mismatches, []);
    assert.deepEqual(track.layout.outside, []);
    assert.equal(Math.round(track.metrics.compliancePct), 100);
    assert.equal(track.layout.isEmpty, false);
    // Тренинг статусом идёт в зачёт и остаётся на полосе своим цветом.
    assert.ok(track.layout.totals.some((item) => item.label === 'Тренинг'));
    const words = describeMyShiftsTrack(track);
    assert.deepEqual([words.mismatchLabel, words.compliance, words.problems, words.outside], ['нет', '100%', [], []]);
});

test('расчёт соответствия отдаёт несовпавшие минуты отрезками, а прежние числа не меняет', () => {
    const { plannerComputeShiftStatusMatchMetrics, buildImportedStatusBarsForDay } = makeScreen({ myScheduleData: null, operatorTodayKey: LATER });
    const metrics = plannerComputeShiftStatusMatchMetrics({
        shiftParts: [{ start: 540, end: 1080, sourceDate: DAY, sourceIndex: 0 }],
        breakParts: BREAKS,
        statusBars: buildImportedStatusBarsForDay({ [DAY]: BAD_DAY }, DAY),
    });
    assert.equal(metrics.totalScheduledMin, 540);
    assert.equal(metrics.scheduledBreakMin, 75);
    assert.equal(metrics.matchedWorkMin, 465 - 17 - 40 - 4 - 10 - 20);
    assert.equal(metrics.matchedBreakMin, 75 - 40);
    assert.equal(metrics.lateTotalMin, 17);
    assert.equal(metrics.earlyLeaveTotalMin, 20);
    assert.equal(metrics.workOutsideShiftMin, 0);
    assert.deepEqual(metrics.mismatchIntervals.map((item) => [item.start, item.end, item.planned]), [
        [540, 557, 'work'], [740, 780, 'work'], [785, 825, 'break'], [870, 874, 'work'], [975, 985, 'work'], [1060, 1080, 'work'],
    ]);
});

test('обоснованное отсутствие идёт в зачёт: тренинг при выключенном телефоне — не несоответствие', () => {
    // Человек с 10:00 до 11:15 на тренинге, телефон выключен — в том числе весь
    // перерыв 11:00–11:15; тренинг заведён в «Тренингах».
    const day = chain(DAY, [
        ['00:00', 'выключен'], ['09:00', 'готов'], ['10:00', 'выключен'], ['11:15', 'готов'], ['13:00', 'перерыв'],
        ['13:45', 'готов'], ['16:00', 'перерыв'], ['16:15', 'готов'], ['18:00', 'выключен'], ['24:00'],
    ]);
    const without = cardOf(day);
    near(without.layout.mismatchMin, 75, 'без тренинга выключенный телефон — несоответствие: час работы и перерыв');
    assert.deepEqual(without.layout.spans[0].mismatches.map((item) => item.tooltip), [
        'Выключен вместо работы • 10:00 — 11:00 • 1ч 00м',
        'Выключен вместо перерыва • 11:00 — 11:15 • 15 мин',
    ]);
    const withTraining = cardOf(day, DAY_SHIFT, {
        plannerTrainingsByOperator: { 4: [{ id: 1, date: DAY, start_time: '10:00', end_time: '11:15' }] },
    });
    assert.equal(withTraining.layout.mismatchMin, 0);
    assert.equal(Math.round(withTraining.metrics.compliancePct), 100);
    // На полосе статусов при этом честно стоит то, что показывал телефон.
    assert.ok(withTraining.layout.totals.some((item) => item.label === 'Выключен'));
});

test('смена без единого статуса внутри: полосы есть, вся смена — несоответствие', () => {
    // Телефон включали только до смены; дальше данных нет вовсе.
    const track = cardOf(chain(DAY, [['00:00', 'выключен'], ['08:00']]), PLAIN_SHIFT);
    assert.notEqual(track, null, 'прятать нельзя: смена прошла мимо графика');
    assert.deepEqual(track.layout.spans[0].bars, []);
    assert.deepEqual(track.layout.spans[0].mismatches.map((item) => item.tooltip), ['Нет статуса вместо работы • 09:00 — 18:00 • 9ч 00м']);
    near(track.layout.mismatchMin, 540, 'вся смена');
    assert.equal(Math.round(track.metrics.compliancePct), 0);
});

/* ── Работа вне смены ─────────────────────────────────────────────────────── */

test('метки вне смены в сумме равны «переработке» из расчёта руководителя', () => {
    // Офлайн-работа после смены оплачивается и в сетке идёт в «Перераб.» — у оператора она же отмечена меткой.
    const track = makeScreen({
        myScheduleData: operator(DAY_SHIFT, { [DAY]: GOOD_DAY }, {
            offlineActivityTimelineDays: { [DAY]: [{ id: 7, startMin: min('19:00'), endMin: min('20:00') }] },
        }),
        operatorTodayKey: LATER,
    }).getMyStatusTrackForDate(DAY);
    assert.deepEqual(track.layout.outside.map((item) => item.tooltip), [
        'Работа до смены • 08:52 — 09:00 • 8 мин',
        'Работа после смены • 18:00 — 18:14 • 14 мин',
        'Работа после смены • 19:00 — 20:00 • 1ч 00м',
    ]);
    near(track.layout.beforeMin + track.layout.afterMin + track.layout.offMin, track.metrics.workOutsideShiftMin, 'та же сумма, что у сетки');
    assert.deepEqual(describeMyShiftsTrack(track).outside.map((item) => item.text), ['до смены 8 мин', 'после смены 1ч 14м']);
});

test('две смены в день: работа между ними относится к ближайшей границе', () => {
    const shifts = { [DAY]: [shift('08:00', '12:00', []), shift('14:00', '18:00', [])] };
    const day = chain(DAY, [['00:00', 'выключен'], ['07:50', 'готов'], ['12:10', 'выключен'], ['13:40', 'готов'], ['18:00', 'выключен'], ['24:00']]);
    const { layout } = cardOf(day, shifts);
    assert.equal(layout.spans.length, 2);
    assert.deepEqual(layout.outside.map((item) => [item.kind, item.startMin, item.endMin]), [
        ['before', min('07:50'), min('08:00')],
        ['after', min('12:00'), min('12:10')],
        ['before', min('13:40'), min('14:00')],
    ]);
    near(layout.beforeMin, 30, 'до смены');
    near(layout.afterMin, 10, 'после смены');
});

test('день без смены: полосы смены нет, работа отмечена «вне смены»', () => {
    const day = chain(DAY, [['00:00', 'выключен'], ['10:00', 'готов'], ['12:30', 'выключен'], ['24:00']]);
    const track = cardOf(day, {});
    assert.deepEqual(track.layout.spans, []);
    assert.deepEqual(track.layout.outside.map((item) => [item.kind, item.tooltip]), [
        ['off', 'Работа вне смены • 10:00 — 12:30 • 2ч 30м'],
    ]);
    near(track.layout.offMin, 150, 'вне смены');
    assert.equal(track.metrics, null, 'сравнивать не с чем — совпадения и третьей ленты нет');
    assert.deepEqual(track.layout.totals, []);
    const words = describeMyShiftsTrack(track);
    assert.deepEqual([words.compliance, words.mismatchLabel, words.outside.map((item) => item.text)], [null, null, ['вне смены 2ч 30м']]);
});

test('показывать нечего — раздел полос не рисует', () => {
    // Выходной, телефон весь день выключен.
    assert.equal(cardOf(chain(DAY, [['00:00', 'выключен'], ['24:00']]), {}), null);
    // Статусов нет вовсе.
    assert.equal(cardOf([], {}), null);
    assert.equal(cardOf([]), null);
    // Данные раздела ещё не приехали.
    assert.equal(makeScreen({ myScheduleData: null, operatorTodayKey: LATER }).getMyStatusTrackForDate(DAY), null);
});

/* ── Ночная смена: два дня одной смены ────────────────────────────────────── */

test('ночная смена: вечер и утро — два дня, опоздание принадлежит вечеру', () => {
    const night = { ...NIGHT, breaks: [{ start: 1500, end: 1530 }] };
    const screen = makeScreen({
        myScheduleData: operator({ [DAY]: [night] }, {
            [DAY]: chain(DAY, [['00:00', 'выключен'], ['21:10', 'готов'], ['24:00']]),
            [NEXT]: chain(NEXT, [['00:00', 'готов'], ['01:05', 'перерыв'], ['01:32', 'готов'], ['09:05', 'выключен'], ['15:00', 'готов'], ['16:30', 'выключен'], ['24:00']]),
        }),
        operatorTodayKey: LATER,
    });

    const evening = screen.getMyStatusTrackForDate(DAY);
    assert.deepEqual(evening.layout.spans.map((span) => [span.startMin, span.endMin]), [[min('21:00'), 1440]]);
    assert.equal(evening.layout.spans[0].late[0].tooltip, 'Опоздание • 21:00 — 21:10 • 10 мин');
    assert.equal(evening.metrics.lateTotalMin, 10);
    assert.equal(evening.metrics.earlyLeaveTotalMin, 0, 'смена доработана до утра — раннего ухода нет');

    const morning = screen.getMyStatusTrackForDate(NEXT);
    assert.deepEqual(morning.layout.spans.map((span) => [span.startMin, span.endMin]), [[0, min('09:00')]]);
    assert.deepEqual(morning.layout.spans[0].late, [], 'опоздание ночной смены на утро не переносится');
    assert.deepEqual(morning.layout.outside.map((item) => [item.kind, item.startMin, item.endMin]), [
        ['after', min('09:00'), min('09:05')],
        ['after', min('15:00'), min('16:30')],
    ]);
    near(morning.layout.afterMin, 95, 'после смены');
});

test('опоздание и ранний уход ночной смены стоят один раз — у дня, в который она началась', () => {
    // На линию человек вышел только в 00:20, ушёл в 08:30.
    const screen = makeScreen({
        myScheduleData: operator({ [DAY]: [NIGHT] }, {
            [DAY]: chain(DAY, [['00:00', 'выключен'], ['24:00']]),
            [NEXT]: chain(NEXT, [['00:00', 'выключен'], ['00:20', 'готов'], ['08:30', 'выключен'], ['24:00']]),
        }),
        operatorTodayKey: LATER,
    });
    // Вечер: считается вся смена, а не её кусок до полуночи.
    const evening = screen.getMyStatusTrackForDate(DAY);
    assert.equal(evening.metrics.lateTotalMin, 200);
    assert.equal(evening.metrics.earlyLeaveTotalMin, 30);
    // Красное упирается в полночь, а подсказка называет окно целиком.
    assert.deepEqual(evening.layout.spans[0].late.map((item) => [item.startMin, item.endMin, item.tooltip]), [
        [min('21:00'), 1440, 'Опоздание • 21:00 — 00:20 • 3ч 20м'],
    ]);
    assert.deepEqual(describeMyShiftsTrack(evening).problems.map((item) => item.text), ['опоздание 3ч 20м', 'ранний уход 30 мин']);
    // Утро: тех же минут второй раз нет ни словом, ни красным на ленте статусов;
    // что не сошлось с графиком, показывает третья лента.
    const morning = screen.getMyStatusTrackForDate(NEXT);
    assert.equal(morning.metrics.lateTotalMin, 0);
    assert.equal(morning.metrics.earlyLeaveTotalMin, 0);
    assert.deepEqual(morning.layout.spans[0].late, []);
    assert.deepEqual(describeMyShiftsTrack(morning).problems, []);
    near(morning.layout.mismatchMin, 50, 'двадцать минут до выхода и полчаса после ухода');
});

test('перерыв по графику через полночь — не «опоздание» следующего утра', () => {
    const night = { ...NIGHT, breaks: [{ start: min('23:50'), end: 1440 + 10 }] };
    const screen = makeScreen({
        myScheduleData: operator({ [DAY]: [night] }, {
            [DAY]: chain(DAY, [['00:00', 'выключен'], ['21:00', 'готов'], ['23:50', 'перерыв'], ['24:00']]),
            [NEXT]: chain(NEXT, [['00:00', 'перерыв'], ['00:10', 'готов'], ['09:00', 'выключен'], ['24:00']]),
        }),
        operatorTodayKey: LATER,
    });
    const morning = screen.getMyStatusTrackForDate(NEXT);
    assert.equal(morning.metrics.lateTotalMin, 0);
    assert.equal(morning.layout.mismatchMin, 0);
    assert.equal(Math.round(morning.metrics.compliancePct), 100);
    assert.deepEqual(describeMyShiftsTrack(morning).problems, []);
});

/* ── Смена, заходящая в сегодня: считаем по момент последних статусов ─────── */

test('сегодня без живого хвоста: смена считается по момент последних статусов', () => {
    // Статусы приехали выгрузкой в 10:05 — как у линии СЗоВ.
    const track = cardOf(chain(DAY, [['00:00', 'выключен'], ['08:58', 'готов'], ['10:05']]), DAY_SHIFT, { today: DAY });
    assert.equal(track.metrics.totalScheduledMin, 65, 'знаменатель — 09:00–10:05, а не вся смена');
    assert.equal(Math.round(track.metrics.compliancePct), 100);
    assert.equal(track.metrics.earlyLeaveTotalMin, 0, 'человек ещё на смене');
    assert.equal(track.layout.mismatchMin, 0, 'ещё не наступившие часы не красим');
    assert.equal(track.asOfLabel, '10:05');
    assert.equal(track.canRefresh, false, 'перечитывать нечего: статусы приезжают выгрузкой');
    // Полоса смены нарисована целиком, статусы заполняют её до 10:05.
    assert.deepEqual([track.layout.spans[0].startMin, track.layout.spans[0].endMin], [540, 1080]);
    assert.equal(Math.max(...track.layout.spans[0].bars.map((bar) => bar.endMin)), min('10:05'));
    near(track.layout.beforeMin, 2, 'ранний вход виден сразу');
});

test('тот же день назавтра считается целиком — как у руководителя в «Графиках работы»', () => {
    const track = cardOf(chain(DAY, [['00:00', 'выключен'], ['08:58', 'готов'], ['10:05']]), DAY_SHIFT, { today: NEXT });
    assert.equal(track.metrics.totalScheduledMin, 540);
    assert.equal(track.metrics.earlyLeaveTotalMin, 475);
    near(track.layout.mismatchMin, 540 - 65, 'несовпавшее — всё после 10:05');
    assert.equal(track.asOfLabel, '');
});

test('смена, кончившаяся ровно в полночь, осталась во вчера — считается целиком', () => {
    // 15:00–00:00, человек ушёл в 20:00; смотрим на следующий день.
    const day = chain(DAY, [['00:00', 'выключен'], ['15:00', 'готов'], ['20:00']]);
    const track = cardOf(day, { [DAY]: [shift('15:00', '00:00', [])] }, { today: NEXT });
    assert.equal(track.metrics.totalScheduledMin, 540);
    assert.equal(track.metrics.earlyLeaveTotalMin, 240);
    assert.equal(track.asOfLabel, '');
});

test('сегодня: смена уже кончилась, а выгрузка отстала — раннего ухода ещё нет', () => {
    // Вечер, смена 09:00–18:00, последняя выгрузка была в 16:05.
    const track = cardOf(chain(DAY, [['00:00', 'выключен'], ['09:00', 'готов'], ['16:05']]), PLAIN_SHIFT, { today: DAY });
    assert.equal(track.metrics.totalScheduledMin, 425);
    assert.equal(track.metrics.earlyLeaveTotalMin, 0);
    assert.equal(track.asOfLabel, '16:05');
});

test('сегодня: смена закончилась и статусы её покрывают — «на HH:MM» не пишем', () => {
    const track = cardOf(GOOD_DAY, DAY_SHIFT, { today: DAY });
    assert.equal(track.asOfLabel, '');
    assert.equal(track.metrics.totalScheduledMin, 540);
    assert.equal(Math.round(track.metrics.compliancePct), 100);
});

test('сегодня: статусы есть только до начала смены — сравнивать ещё не с чем', () => {
    // Смена с 12:00; человек с утра полчаса был на линии.
    const day = chain(DAY, [['00:00', 'выключен'], ['08:00', 'готов'], ['08:30', 'выключен'], ['08:40']]);
    const track = cardOf(day, { [DAY]: [shift('12:00', '21:00', [])] }, { today: DAY });
    assert.equal(track.metrics, null, 'ни совпадения, ни третьей ленты');
    assert.equal(track.layout.mismatchMin, 0);
    assert.deepEqual(track.layout.outside.map((item) => [item.kind, item.startMin, item.endMin]), [['before', min('08:00'), min('08:30')]]);
    assert.equal(track.asOfLabel, '08:40');
    assert.equal(describeMyShiftsTrack(track).mismatchLabel, null);
});

test('сегодня: запланированная на вечер офлайн-работа внутри смены не становится меткой «после смены»', () => {
    // Статусы до 10:05, а на 15:00–16:00 руководитель уже завёл офлайн-активность.
    const track = makeScreen({
        myScheduleData: operator(DAY_SHIFT, { [DAY]: chain(DAY, [['00:00', 'выключен'], ['08:58', 'готов'], ['10:05']]) }, {
            offlineActivityTimelineDays: { [DAY]: [{ id: 7, startMin: min('15:00'), endMin: min('16:00') }] },
        }),
        operatorTodayKey: DAY,
    }).getMyStatusTrackForDate(DAY);
    assert.deepEqual(track.layout.outside.map((item) => [item.kind, item.startMin, item.endMin]), [['before', min('08:58'), min('09:00')]]);
    assert.equal(track.layout.afterMin, 0);
});

test('ночная смена кончается сегодня: вечер и утро считаются по один и тот же момент', () => {
    // Смена 21:00–09:00, последняя выгрузка дошла до 06:00 утра.
    const data = operator({ [DAY]: [NIGHT] }, {
        [DAY]: chain(DAY, [['00:00', 'выключен'], ['21:00', 'готов'], ['24:00']]),
        [NEXT]: chain(NEXT, [['00:00', 'готов'], ['06:00']]),
    });
    const today = makeScreen({ myScheduleData: data, operatorTodayKey: NEXT });
    // Вечерняя карточка (вчера): смена ещё заходит в сегодня — раннего ухода нет.
    const evening = today.getMyStatusTrackForDate(DAY);
    assert.equal(evening.asOfLabel, '06:00', 'момент данных — 06:00 утра, а не конец вчерашних суток');
    assert.equal(evening.metrics.totalScheduledMin, 180, 'вечер известен целиком');
    assert.equal(evening.metrics.earlyLeaveTotalMin, 0);
    assert.equal(evening.layout.mismatchMin, 0);
    // Утренняя карточка (сегодня) говорит то же самое.
    const morning = today.getMyStatusTrackForDate(NEXT);
    assert.equal(morning.asOfLabel, '06:00');
    assert.equal(morning.metrics.totalScheduledMin, 360);
    assert.equal(morning.layout.mismatchMin, 0);
    // Через день та же смена считается целиком: ушёл в 06:00 — ранний уход три часа.
    const later = makeScreen({ myScheduleData: data, operatorTodayKey: LATER });
    assert.equal(later.getMyStatusTrackForDate(DAY).metrics.earlyLeaveTotalMin, 180);
    assert.equal(later.getMyStatusTrackForDate(DAY).asOfLabel, '');
    near(later.getMyStatusTrackForDate(NEXT).layout.mismatchMin, 180, 'три часа утра без статуса');
});

test('ночная смена, выгрузка остановилась вечером: вчерашний вечер не считается проваленным', () => {
    const track = cardOf(chain(DAY, [['00:00', 'выключен'], ['21:00', 'готов'], ['22:05']]), { [DAY]: [NIGHT] }, { today: NEXT });
    assert.equal(track.metrics.totalScheduledMin, 65);
    assert.equal(track.metrics.earlyLeaveTotalMin, 0);
    assert.equal(track.layout.mismatchMin, 0);
    assert.equal(track.asOfLabel, '22:05');
});

/* ── Живой канал (Тез КЦ) ─────────────────────────────────────────────────── */

const LIVE_CLOSED = chain(DAY, [['09:06', 'готов'], ['10:02', 'перерыв'], ['10:09']]);
const LIVE_TAIL = seg(DAY, '10:09', '10:51', 'готов', { isLiveTail: true });

test('живой хвост главнее: потолок — серверное «сейчас», данные берутся из свежего среза', () => {
    // Запрос периода пришёл при открытии раздела (один отрезок и старый хвост),
    // живой срез раз в полминуты привозит день заново и текущий статус.
    const track = makeScreen({
        myScheduleData: operator({ [DAY]: [shift('09:00', '19:00', [])] }, { [DAY]: chain(DAY, [['09:06', 'готов'], ['09:20']]) }, {
            liveStatusTail: seg(DAY, '09:20', '09:21', 'готов', { isLiveTail: true }),
        }),
        myStatusTrackLive: { asOf: `${DAY}T10:51:07`, timelineDays: { [DAY]: LIVE_CLOSED }, tail: LIVE_TAIL },
        operatorTodayKey: DAY,
    }).getMyStatusTrackForDate(DAY);
    assert.equal(track.metrics.totalScheduledMin, 111, 'знаменатель — 09:00–10:51');
    assert.equal(track.metrics.lateTotalMin, 6);
    near(track.layout.mismatchMin, 13, 'опоздание 6 минут и 7 минут перерыва не по графику');
    assert.equal(track.asOfLabel, '10:51');
    assert.equal(track.canRefresh, true);
    assert.equal(Math.max(...track.layout.spans[0].bars.map((bar) => bar.endMin)), min('10:51'));
});

test('живой канал без текущего статуса: подпись называет момент, по который посчитано на самом деле', () => {
    // Последнее событие сервер текущим не считает (хвоста нет) — расчёт идёт по конец закрытых статусов.
    const track = makeScreen({
        myScheduleData: operator({ [DAY]: [shift('09:00', '19:00', [])] }, { [DAY]: LIVE_CLOSED }),
        myStatusTrackLive: { asOf: `${DAY}T12:00:07`, timelineDays: { [DAY]: LIVE_CLOSED }, tail: null },
        operatorTodayKey: DAY,
    }).getMyStatusTrackForDate(DAY);
    assert.equal(track.metrics.totalScheduledMin, 69, 'посчитано до 10:09');
    assert.equal(track.asOfLabel, '10:09', 'не «на 12:00»');
    assert.equal(track.canRefresh, true, 'канал живой — перечитать можно');
});

test('вчерашний день рядом с живым сегодняшним: ни времени «на HH:MM», ни кнопки обновления', () => {
    const track = makeScreen({
        myScheduleData: operator(
            { [PREV]: [shift('09:00', '18:00', [])], [DAY]: [shift('09:00', '19:00', [])] },
            { [PREV]: chain(PREV, [['00:00', 'выключен'], ['09:00', 'готов'], ['18:00', 'выключен'], ['24:00']]), [DAY]: LIVE_CLOSED }
        ),
        myStatusTrackLive: { asOf: `${DAY}T10:51:07`, timelineDays: { [DAY]: LIVE_CLOSED }, tail: LIVE_TAIL },
        operatorTodayKey: DAY,
    }).getMyStatusTrackForDate(PREV);
    assert.equal(track.asOfLabel, '');
    assert.equal(track.canRefresh, false);
    assert.equal(track.metrics.totalScheduledMin, 540);
    assert.equal(Math.round(track.metrics.compliancePct), 100);
});

test('ночная смена в живом отделе: вчерашний вечер упирается в текущий статус', () => {
    // Смена началась вчера в 21:00, сейчас 03:10 — хвост лежит в сегодняшних сутках.
    const closed = chain(NEXT, [['00:00', 'готов'], ['02:00']]);
    const tail = seg(NEXT, '02:00', '03:10', 'готов', { isLiveTail: true });
    const track = makeScreen({
        myScheduleData: operator({ [DAY]: [NIGHT] }, { [DAY]: chain(DAY, [['00:00', 'выключен'], ['21:00', 'готов'], ['24:00']]) }),
        myStatusTrackLive: { asOf: `${NEXT}T03:10:02`, timelineDays: { [NEXT]: closed }, tail },
        operatorTodayKey: NEXT,
    }).getMyStatusTrackForDate(DAY);
    assert.equal(track.asOfLabel, '03:10');
    assert.equal(track.canRefresh, true);
    assert.equal(track.metrics.totalScheduledMin, 180);
    assert.equal(track.metrics.earlyLeaveTotalMin, 0);
});

test('явный потолок расчёта не трогает остальные экраны: без параметра смена считается целиком', () => {
    const { plannerComputeShiftStatusMatchMetrics, buildImportedStatusBarsForDay } = makeScreen({ myScheduleData: null, operatorTodayKey: LATER });
    const args = {
        shiftParts: [{ start: 540, end: 1080, sourceDate: DAY, sourceIndex: 0 }],
        breakParts: [],
        statusBars: buildImportedStatusBarsForDay({ [DAY]: chain(DAY, [['09:00', 'готов'], ['10:05']]) }, DAY),
    };
    const whole = plannerComputeShiftStatusMatchMetrics(args);
    assert.equal(whole.totalScheduledMin, 540);
    assert.equal(whole.earlyLeaveTotalMin, 475);
    const clamped = plannerComputeShiftStatusMatchMetrics({ ...args, clampEndMin: 605 });
    assert.equal(clamped.totalScheduledMin, 65);
    assert.equal(clamped.earlyLeaveTotalMin, 0);
    assert.deepEqual(clamped.mismatchIntervals, []);
    // null и мусор — то же, что без параметра.
    assert.equal(plannerComputeShiftStatusMatchMetrics({ ...args, clampEndMin: null }).totalScheduledMin, 540);
    assert.equal(plannerComputeShiftStatusMatchMetrics({ ...args, clampEndMin: 'x' }).totalScheduledMin, 540);
    // Живой хвост точнее и поэтому главнее: при обоих потолках считается по хвосту.
    const withTail = {
        ...args,
        statusBars: buildImportedStatusBarsForDay({
            [DAY]: [...chain(DAY, [['09:00', 'готов'], ['10:05']]), seg(DAY, '10:05', '10:30', 'готов', { isLiveTail: true })],
        }, DAY),
    };
    assert.equal(plannerComputeShiftStatusMatchMetrics({ ...withTail, clampEndMin: 605 }).totalScheduledMin, 90);
    assert.equal(plannerComputeShiftStatusMatchMetrics({ ...withTail, clampEndMin: 900 }).totalScheduledMin, 90);
});

/* ── Касание и слова ──────────────────────────────────────────────────────── */

test('касание выбирает сплошной участок одного цвета, а не отдельный статус', () => {
    const [span] = cardOf(BAD_DAY).layout.spans;
    // «Готов» и «Занят» одного цвета и стоят встык — это один участок.
    const first = span.runs[0];
    assert.equal(first.tooltip, 'Готов, Занят • 09:17 — 11:00 • 1ч 43м');
    // Подпись выбранного касанием начинается со времени: в узкой строке телефона
    // под многоточие уходит название, а не «когда» и «сколько».
    assert.equal(first.caption, '09:17–11:00 · 1ч 43м · Готов, Занят');
    assert.equal(span.late[0].caption, '09:00–09:17 · 17 мин · Опоздание');
    assert.equal(span.mismatches[2].caption, '13:05–13:45 · 40 мин · Готов, Занят вместо перерыва');
    assert.equal(cardOf(GOOD_DAY).layout.outside[1].caption, '18:00–18:14 · 14 мин · Работа после смены');
    assert.deepEqual(span.bars.filter((bar) => bar.runKey === first.key).map((bar) => bar.label), ['Готов', 'Занят', 'Готов']);
    // Перерыв другого цвета — свой участок, и каждый отрезок знает свой.
    assert.equal(span.runs[1].tooltip, 'Перерыв • 11:00 — 11:15 • 15 мин');
    span.bars.forEach((bar) => assert.ok(span.runs.some((run) => run.key === bar.runKey), bar.tooltip));
    // Подсказка отрезка на компьютере — про сам статус, целиком.
    assert.equal(span.bars[0].tooltip, 'Готов • 09:17 — 10:10');
});

test('участок рвётся на дыре без статуса; в подписи не больше трёх названий', () => {
    // Десять минут без единого статуса между двумя «Готов» — два участка, а не один.
    const holed = [...chain(DAY, [['09:00', 'готов'], ['10:00']]), ...chain(DAY, [['10:10', 'готов'], ['18:00']])];
    assert.deepEqual(cardOf(holed, PLAIN_SHIFT).layout.spans[0].runs.map((run) => [run.startMin, run.endMin]), [
        [min('09:00'), min('10:00')],
        [min('10:10'), min('18:00')],
    ]);
    // Четыре статуса одного цвета подряд: три самых долгих и многоточие.
    const busy = chain(DAY, [['09:00', 'готов'], ['12:00', 'занят'], ['14:00', 'перезвон'], ['15:00', 'занята'], ['15:30', 'готов'], ['18:00']]);
    const [run] = cardOf(busy, PLAIN_SHIFT).layout.spans[0].runs;
    assert.equal(run.tooltip, 'Готов, Занят, Перезвон … • 09:00 — 18:00 • 9ч 00м');
});

test('выбор под пальцем: узкому отрезку достраивается зона касания', () => {
    const wide = { key: 'wide', startMin: 600, endMin: 900 };
    const thin = { key: 'thin', startMin: 870, endMin: 874 };
    const other = { key: 'other', startMin: 950, endMin: 952 };
    const segments = [wide, thin, other];
    // Палец — 56 минут оси (14 px на телефоне при полосе 358 px).
    assert.equal(pickTrackSegment(segments, 700, 56).key, 'wide', 'широкий берётся там, где он нарисован');
    assert.equal(pickTrackSegment(segments, 885, 56).key, 'thin', 'рядом с узким выигрывает узкий');
    assert.equal(pickTrackSegment(segments, 930, 56).key, 'other', 'из двух узких — ближайший');
    assert.equal(pickTrackSegment(segments, 846, 56).key, 'thin', 'зона узкого — 28 минут в каждую сторону от середины');
    assert.equal(pickTrackSegment(segments, 843, 56).key, 'wide', 'за её краем снова широкий');
    assert.equal(pickTrackSegment(segments, 905, 56), null, 'между зонами — ничего');
    assert.equal(pickTrackSegment(segments, 1100, 56), null, 'мимо всех — ничего');
    assert.equal(pickTrackSegment(segments, 1000, 0), null, 'без допуска узкий берётся только точным попаданием');
    assert.equal(pickTrackSegment(segments, 951, 0).key, 'other');
    assert.equal(pickTrackSegment([], 700, 56), null);
    assert.equal(pickTrackSegment(segments, Number.NaN, 56), null);
});

test('касание по ряду: пиксель → минута оси → отрезок; второе касание и касание мимо снимают выбор', () => {
    const [span] = cardOf(BAD_DAY).layout.spans;
    const segments = [...span.late, ...span.runs];
    const width = 358;
    const at = (hhmm) => (min(hhmm) / 1440) * width;
    const tap = (offsetX, current = null, row = 'status') => resolveTrackTap({ row, segments, offsetX, width, targetPx: 14, current });
    // 09:08 — середина красного опоздания (оно шириной 4 px).
    assert.deepEqual(tap(at('09:08')), { row: 'status', key: span.late[0].key });
    // 12:40 — обед не по графику.
    assert.deepEqual(tap(at('12:40')), { row: 'status', key: span.runs.find((run) => run.startMin === min('12:20')).key });
    // 14:45 — рядом с «без телефона» шириной в пиксель: берётся он, а не широкий сосед.
    assert.deepEqual(tap(at('14:45')), { row: 'status', key: span.runs.find((run) => run.startMin === min('14:30')).key });
    // Второе касание того же отрезка снимает выбор; тот же ключ в другом ряду — это другой выбор.
    const picked = { row: 'status', key: span.late[0].key };
    assert.equal(tap(at('09:08'), picked), null);
    assert.deepEqual(tap(at('09:08'), picked, 'mismatch'), { row: 'mismatch', key: span.late[0].key });
    // Мимо всех (03:00 — до смены) — выбор снят; ряд нулевой ширины ничего не меняет.
    assert.equal(tap(at('03:00'), picked), null);
    assert.equal(resolveTrackTap({ row: 'status', segments, offsetX: 10, width: 0, targetPx: 14, current: picked }), picked);
});

test('слова под лентами: печатается только то, что есть, и теми же порогами, что на полосе', () => {
    const words = (layout, metrics = null) => describeMyShiftsTrack({ layout, metrics });
    const none = { beforeMin: 0, afterMin: 0, offMin: 0, mismatchMin: 0 };
    const ok = { compliancePct: 100, lateTotalMin: 0, earlyLeaveTotalMin: 0 };
    // Итог третьей ленты.
    assert.equal(words(none, ok).mismatchLabel, 'нет');
    assert.equal(words({ ...none, mismatchMin: 0.3 }, ok).mismatchLabel, 'меньше минуты');
    assert.equal(words({ ...none, mismatchMin: 1 }, ok).mismatchLabel, '1 мин');
    assert.equal(words({ ...none, mismatchMin: 131 }, ok).mismatchLabel, '2ч 11м');
    assert.equal(words({ ...none, mismatchMin: 131 }, null).mismatchLabel, null, 'без метрик третьей ленты нет');
    // Совпадение.
    assert.equal(words(none, { ...ok, compliancePct: 75.6 }).compliance, '76%');
    assert.equal(words(none, { ...ok, compliancePct: null }).compliance, '—');
    assert.equal(words(none, null).compliance, null);
    // Отклонения: меньше полминуты не печатаются, час и больше — часами.
    assert.deepEqual(words(none, { ...ok, lateTotalMin: 0.4, earlyLeaveTotalMin: 0 }).problems, []);
    assert.deepEqual(words(none, { ...ok, lateTotalMin: 0.6, earlyLeaveTotalMin: 75 }).problems, [
        { key: 'late', text: 'опоздание 1 мин' },
        { key: 'early', text: 'ранний уход 1ч 15м' },
    ]);
    assert.deepEqual(words(none, null).problems, []);
    // Работа вне смены: без порога «переработки» в десять минут.
    assert.deepEqual(words({ ...none, beforeMin: 0.4, afterMin: 0.6, offMin: 0 }, ok).outside, [{ key: 'after', text: 'после смены 1 мин' }]);
    assert.deepEqual(words({ ...none, beforeMin: 8, afterMin: 14, offMin: 150 }, null).outside, [
        { key: 'before', text: 'до смены 8 мин' },
        { key: 'after', text: 'после смены 14 мин' },
        { key: 'off', text: 'вне смены 2ч 30м' },
    ]);
});

test('время и длительность — теми же словами, что печатает раздел', () => {
    assert.equal(formatTrackTime(557), '09:17');
    assert.equal(formatTrackTime(1440), '00:00');
    assert.equal(formatTrackTime(1500), '01:00');
    assert.equal(formatTrackDuration(0.4), '0 мин');
    assert.equal(formatTrackDuration(17), '17 мин');
    assert.equal(formatTrackDuration(60), '1ч 00м');
    assert.equal(formatTrackDuration(131), '2ч 11м');
});

/* ── Разметка: настоящий компонент через react-dom/server ─────────────────── */

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');

const CACHE = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(CACHE, { recursive: true });
const OUT = join(CACHE, 'MyShiftsMobile.mjs');
buildSync({
    entryPoints: [join(process.cwd(), 'src', 'components', 'schedule', 'MyShiftsMobile.jsx')],
    bundle: true,
    format: 'esm',
    platform: 'node',
    target: 'node18',
    outfile: OUT,
    jsx: 'transform',
    loader: { '.js': 'jsx', '.css': 'empty' },
    external: ['react', 'react-dom', 'react-dom/*', 'lucide-react'],
    logLevel: 'silent',
});
const { MyShiftsStatusTrack } = await import(pathToFileURL(OUT).href);

/* Пропсы — те же, что собирает renderMyStatusTrack в App.jsx: раскладка и слова. */
const renderTrack = (track, props = {}) => renderToStaticMarkup(React.createElement(MyShiftsStatusTrack, {
    spans: track.layout.spans,
    outside: track.layout.outside,
    totals: track.layout.totals.map((item) => ({ ...item, value: formatTrackDuration(item.minutes) })),
    mismatchLabel: describeMyShiftsTrack(track).mismatchLabel,
    ...props,
}));
const count = (html, needle) => html.split(needle).length - 1;

test('разметка: две ленты факта стоят под сменой, третья — после статусов', () => {
    const track = cardOf(BAD_DAY);
    const html = renderTrack(track);
    const statuses = html.indexOf('>Статусы<');
    const mismatches = html.indexOf('>Несоответствия<');
    assert.ok(statuses !== -1 && mismatches > statuses, 'сначала статусы, потом несоответствия');
    // Обе полосы смены стоят в одном и том же месте оси суток.
    assert.equal(count(html, 'left:37.5%;width:37.5%'), 2);
    assert.ok(html.includes('>2ч 11м<'), 'итог несоответствий — в строке заголовка');
    assert.equal(count(html, 'bg-rose-500'), 1 + track.layout.spans[0].mismatches.length, 'красное — опоздание и несоответствия');
    assert.ok(html.includes('data-schedule-tooltip="Опоздание • 09:00 — 09:17 • 17 мин"'));
    assert.ok(html.includes('data-schedule-tooltip="Перерыв вместо работы • 12:20 — 13:00 • 40 мин"'));
    // Узкие отрезки не исчезают: у статуса и опоздания — не уже 2 px, у несоответствия — не уже 1 px.
    assert.equal(count(html, 'min-width:2px'), track.layout.spans[0].bars.length + 1);
    assert.equal(count(html, 'min-width:1px'), track.layout.spans[0].mismatches.length);
    assert.equal(count(html, 'opacity-30'), 0, 'пока ничего не выбрано, ничего не гаснет');
});

test('разметка: узкие статусы рисуются поверх широких — их не закрывает сосед', () => {
    // Минимальная ширина выводит узкий кусок за его настоящий край; сосед,
    // нарисованный позже, закрыл бы этот запас вместе с подсказкой.
    const track = cardOf(BAD_DAY);
    const html = renderTrack(track);
    const order = track.layout.spans[0].bars
        .map((bar) => ({ bar, at: html.indexOf(`data-schedule-tooltip="${bar.tooltip}"`) }))
        .sort((a, b) => a.at - b.at)
        .map((item) => item.bar);
    assert.ok(order.every((bar, index) => index === 0 || bar.width <= order[index - 1].width), 'от широких к узким');
    assert.equal(order[order.length - 1].tooltip, 'Без телефона • 14:30 — 14:34');
    // Красное опоздание — последним в слое, поверх любого статуса.
    assert.ok(html.indexOf('data-schedule-tooltip="Опоздание') > html.indexOf('data-schedule-tooltip="Без телефона'));
});

test('разметка: на телефоне подсказок по наведению нет — отрезок выбирают касанием', () => {
    const track = cardOf(BAD_DAY);
    assert.ok(count(renderTrack(track), 'data-schedule-tooltip=') > 10);
    assert.equal(count(renderTrack(track), 'ms-m-tap'), 0, 'на компьютере ряды не ловят касания');
    const phone = renderTrack(track, { touch: true });
    assert.equal(count(phone, 'data-schedule-tooltip='), 0);
    assert.equal(count(phone, 'ms-m-tap'), 2);
});

test('разметка: выбранный участок статусов встаёт в заголовок и рисуется поверх погашенного слоя', () => {
    const track = cardOf(BAD_DAY);
    const [span] = track.layout.spans;
    const run = span.runs[0];
    const html = renderTrack(track, { touch: true, asOf: '10:51', onRefresh: () => {}, defaultPicked: { row: 'status', key: run.key } });
    assert.ok(html.includes(`>${run.caption}<`), 'подпись участка — в строке заголовка');
    assert.ok(!html.includes('>Статусы<'), 'название ленты уступило место подписи');
    assert.equal(count(html, 'opacity-30'), 1, 'гаснет один слой целиком, а не отрезки по одному');
    // Отрезки участка нарисованы дважды: в погашенном слое и поверх него.
    const piecesInRun = span.bars.filter((bar) => bar.runKey === run.key).length;
    assert.equal(piecesInRun, 3);
    assert.equal(count(html, 'calc('), span.bars.length + piecesInRun);
    // Время и кнопка уступают строку выбранному отрезку, а высота строки держится по кнопке.
    assert.ok(!html.includes('на 10:51') && !html.includes('Обновить статусы'));
    assert.ok(html.includes('min-height:24px'));
    const idle = renderTrack(track, { touch: true, asOf: '10:51', onRefresh: () => {} });
    assert.ok(idle.includes('на 10:51') && idle.includes('Обновить статусы') && idle.includes('min-height:24px'));
    // Выбранное опоздание — тоже поверх слоя.
    const late = renderTrack(track, { touch: true, defaultPicked: { row: 'status', key: span.late[0].key } });
    assert.ok(late.includes('>09:00–09:17 · 17 мин · Опоздание<'));
    assert.equal(count(late, 'bg-rose-500'), 2 + span.mismatches.length + 1, 'опоздание дважды, несоответствия и точка в заголовке');
});

test('разметка: выбранное несоответствие встаёт в заголовок вместо итога, остальные гаснут', () => {
    const track = cardOf(BAD_DAY);
    const [span] = track.layout.spans;
    const picked = span.mismatches[1];
    const html = renderTrack(track, { touch: true, defaultPicked: { row: 'mismatch', key: picked.key } });
    assert.ok(html.includes(`>${picked.caption}<`));
    assert.ok(!html.includes('>Несоответствия<') && !html.includes('>2ч 11м<'));
    // Ряд одноцветный — точки цвета в подписи нет, её место отдано словам.
    assert.equal(count(html, 'h-2 w-2 shrink-0 rounded-full'), track.layout.totals.length, 'точки остались только в легенде статусов');
    assert.ok(html.includes('>Статусы<'), 'соседняя лента не тронута');
    assert.equal(count(html, 'opacity-30'), span.mismatches.length - 1);
    // Отрезка с таким ключом больше нет (данные обновились) — выбор молча снят.
    const stale = renderTrack(track, { touch: true, defaultPicked: { row: 'mismatch', key: 'mismatch-9-9' } });
    assert.ok(stale.includes('>Несоответствия<') && stale.includes('>2ч 11м<'));
    assert.equal(count(stale, 'opacity-30'), 0);
});

test('разметка: метки работы вне смены — зелёные, «раньше» держится за правый край', () => {
    const track = cardOf(GOOD_DAY);
    const html = renderTrack(track);
    assert.equal(count(html, 'bg-emerald-500'), 2);
    // 08:52–09:00: правый край метки совпадает с началом смены (100% − 37.5%).
    assert.ok(html.includes('right:62.5%'), 'метка «до смены» растёт влево от смены');
    assert.ok(html.includes('data-schedule-tooltip="Работа после смены • 18:00 — 18:14 • 14 мин"'));
    assert.equal(count(html, 'min-width:3px'), 2);
});

test('разметка: у дня без смены третьей ленты нет, у пустого дня нет ничего', () => {
    const day = chain(DAY, [['00:00', 'выключен'], ['10:00', 'готов'], ['12:30', 'выключен'], ['24:00']]);
    const html = renderTrack(cardOf(day, {}));
    assert.ok(html.includes('>Статусы<'));
    assert.ok(!html.includes('Несоответствия'), 'сравнивать не с чем');
    assert.equal(count(html, 'bg-emerald-500'), 1);
    assert.equal(renderToStaticMarkup(React.createElement(MyShiftsStatusTrack, { spans: [], outside: [] })), '');
    // Смена есть, но статусы до неё ещё не дошли: метка «до смены» есть, третьей ленты нет.
    const early = cardOf(chain(DAY, [['00:00', 'выключен'], ['08:00', 'готов'], ['08:30', 'выключен'], ['08:40']]), { [DAY]: [shift('12:00', '21:00', [])] }, { today: DAY });
    assert.ok(!renderTrack(early).includes('Несоответствия'));
});
