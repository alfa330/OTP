import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/*
 * Задача #330: полоса статусов и соответствие «в реальном времени» у Тез КЦ.
 *
 * Считает это plannerComputeShiftStatusMatchMetrics внутри огромного App.jsx,
 * поэтому тест вынимает из исходника настоящий расчёт вместе с помощниками и
 * гоняет их на своих данных — второй формулы соответствия в проекте нет.
 *
 * Что здесь стережётся. Идущая смена считается ДО момента живого хвоста: без
 * этого у смены 09:00–19:00 в 11:30 знаменателем шли бы все десять часов, и
 * человек, который работает прямо сейчас, видел бы «совпадение 22%» и «ранний
 * уход 451 минута». Ровно это и было на проде до правки.
 */

const APP = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');

function slice(source, start, end) {
    const from = source.indexOf(start);
    assert.notEqual(from, -1, `нет начала: ${start}`);
    const to = source.indexOf(end, from + start.length);
    assert.notEqual(to, -1, `нет конца: ${end}`);
    return source.slice(from, to);
}

const dates = slice(APP, 'const todayDateStr = (d = new Date()) => {', '// Прошедшие перерывы не пересчитываются');
const intervals = slice(APP, 'const mergeIntervals = (arr) => {', 'const plannerStatusKeyLabelMap = {');
const statusKeys = slice(APP, 'const PLANNER_IMPORTED_WORK_STATUS_KEYS = new Set([', 'const plannerStatusFormatDuration = (seconds) => {');
const dayKey = slice(APP, 'const plannerStatusDayKey = (value) => {', 'const plannerStatusFormatDayLabel = (dayKey) => {');
const dayMinutes = slice(APP, 'const plannerStatusImportedSegmentToDayMinutes = (seg, dateKey) => {', 'const buildImportedStatusContextBarsForDay = (timelineByDay, dateKey) => {');
const liveTail = slice(APP, 'const plannerTimelineDaysWithLiveTail = (op) => {', 'const buildPlannerStatusAnalysisFromOperators = (operatorsList = []) => {');

const build = new Function(`
    ${dates}
    ${intervals}
    ${statusKeys}
    ${dayKey}
    ${dayMinutes}
    ${liveTail}
    return { plannerComputeShiftStatusMatchMetrics, buildImportedStatusBarsForDay, plannerTimelineDaysWithLiveTail };
`);
const { plannerComputeShiftStatusMatchMetrics, buildImportedStatusBarsForDay, plannerTimelineDaysWithLiveTail } = build();

const DAY = '2026-09-21';
const at = (hhmm, seconds = 0) => `${DAY}T${hhmm}:${String(seconds).padStart(2, '0')}`;
const segment = (statusKey, from, to, extra = {}) => ({
    statusDate: DAY,
    start: at(from),
    end: at(to),
    durationSec: 0,
    stateName: statusKey,
    stateKey: statusKey,
    stateNote: '',
    isWork: statusKey === 'готов' || statusKey === 'занят',
    isBreak: statusKey === 'перерыв',
    isTraining: false,
    isLateStart: statusKey === 'готов',
    isTechnicalReason: false,
    isLateExcused: false,
    isNoPhone: false,
    ...extra,
});

// Смена 09:00–19:00, человек работает с 09:00 и в 11:30 всё ещё на линии:
// в базе лежит закрытый кусок 09:00–11:29, текущий статус приезжает хвостом.
const SHIFT = [{ start: 9 * 60, end: 19 * 60, sourceDate: DAY, sourceIndex: 0 }];
const CLOSED = { [DAY]: [segment('готов', '09:00', '11:29')] };
const tail = (to) => segment('готов', '11:29', to, { isLiveTail: true });

const metricsFor = (timelineDays, liveStatusTail) => {
    const timeline = plannerTimelineDaysWithLiveTail({
        importedStatusTimelineDays: timelineDays,
        liveStatusTail,
    });
    return plannerComputeShiftStatusMatchMetrics({
        shiftParts: SHIFT,
        breakParts: [],
        statusBars: buildImportedStatusBarsForDay(timeline, DAY),
    });
};

test('идущая смена считается до «сейчас», а не на всю длину', () => {
    const было = metricsFor(CLOSED, null);
    assert.equal(Math.round(было.totalScheduledMin), 600);
    assert.ok(было.compliancePct < 30, 'без хвоста знаменатель — вся смена');

    const стало = metricsFor(CLOSED, tail('11:30'));
    assert.equal(Math.round(стало.totalScheduledMin), 150, 'знаменатель — 09:00–11:30');
    assert.equal(Math.round(стало.compliancePct), 100);
});

test('у идущей смены нет раннего ухода — человек ещё работает', () => {
    const было = metricsFor(CLOSED, null);
    assert.ok(было.earlyLeaveTotalMin > 400, 'без хвоста уход считался до конца смены');
    const стало = metricsFor(CLOSED, tail('11:30'));
    assert.equal(стало.earlyLeaveTotalMin, 0);
});

test('опоздание считается по-прежнему: хвост не прощает поздний вход', () => {
    const поздний = { [DAY]: [segment('готов', '09:20', '11:29')] };
    const m = metricsFor(поздний, tail('11:30'));
    assert.equal(Math.round(m.lateTotalMin), 20);
});

test('закончившаяся смена считается целиком: потолок хвоста — её конец', () => {
    // Сервер обрезает хвост концом смены, поэтому в 19:30 он доходит до 19:00.
    const m = metricsFor(CLOSED, segment('готов', '11:29', '19:00', { isLiveTail: true }));
    assert.equal(Math.round(m.totalScheduledMin), 600, 'смена кончилась — знаменатель полный');
    assert.equal(Math.round(m.compliancePct), 100);
    assert.equal(m.earlyLeaveTotalMin, 0);
});

test('смена, которая сегодня ещё не начиналась, из разбора выпадает', () => {
    const вечерняя = [{ start: 21 * 60, end: 24 * 60, sourceDate: DAY, sourceIndex: 0 }];
    const timeline = plannerTimelineDaysWithLiveTail({
        importedStatusTimelineDays: CLOSED,
        liveStatusTail: tail('11:30'),
    });
    const m = plannerComputeShiftStatusMatchMetrics({
        shiftParts: вечерняя,
        breakParts: [],
        statusBars: buildImportedStatusBarsForDay(timeline, DAY),
    });
    assert.equal(m.perShift.length, 0, 'ноль процентов и «уход во всю смену» — неправда');
    assert.equal(m.totalScheduledMin, 0);
    assert.equal(m.compliancePct, null);
});

test('без живого хвоста расчёт прежний: чужие отделы и вчерашние дни не трогаем', () => {
    const вчерашний = metricsFor({ [DAY]: [segment('готов', '09:00', '19:00')] }, null);
    assert.equal(Math.round(вчерашний.totalScheduledMin), 600);
    assert.equal(Math.round(вчерашний.compliancePct), 100);
});

test('хвост попадает в карту дня один раз и только со своей датой', () => {
    const t = tail('11:30');
    const timeline = plannerTimelineDaysWithLiveTail({ importedStatusTimelineDays: CLOSED, liveStatusTail: t });
    assert.equal(timeline[DAY].length, 2);
    assert.equal(timeline[DAY].filter((seg) => seg.isLiveTail).length, 1);
    // Без хвоста карта возвращается как есть — лишней копии не делаем.
    assert.equal(plannerTimelineDaysWithLiveTail({ importedStatusTimelineDays: CLOSED }), CLOSED);
});
