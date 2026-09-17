import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { calculateWeightedChatAverage } from '../src/utils/chatScore.js';

/*
 * Учёт часов, ТЗ #344: строка «Итого» показывает итог каждого дня. Считает его
 * useMemo внутри огромного компонента App.jsx, поэтому тест вынимает из
 * исходника сам расчёт и настоящие помощники раздела (длительность тренингов,
 * техсбоев, офлайна, «без телефона», формат денег) и гоняет их на своих данных.
 * Ошибка здесь не падает, а тихо показывает под днём не ту сумму.
 */

const APP = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');

function slice(source, start, end) {
    const from = source.indexOf(start);
    assert.notEqual(from, -1, `нет начала: ${start}`);
    const to = source.indexOf(end, from + start.length);
    assert.notEqual(to, -1, `нет конца: ${end}`);
    return source.slice(from, to);
}

const helpers = slice(APP, 'function parseTimeToMinutes(t) {', 'function getNoPhoneHoursForRow(op) {');
const formatMoney = slice(APP, 'function formatMoney(n) {', 'function getFineIcon(reason) {');
const memoHead = 'const hoursDayTotals = useMemo(() => {';
const memoBody = slice(APP, memoHead, '}, [filteredOperators, daysArray, selectedTab, selectedGroupId,').slice(memoHead.length);

const computeDayTotals = new Function(
    'calculateWeightedChatAverage',
    'ctx',
    `${helpers}\n${formatMoney}\n`
    + 'const { filteredOperators, daysArray, selectedTab, selectedGroupId, trainingsMap, technicalIssuesMap, offlineActivitiesMap, tezSuccessMap } = ctx;\n'
    + memoBody,
);

const totals = (overrides) => computeDayTotals(calculateWeightedChatAverage, {
    filteredOperators: [],
    daysArray: [1, 2, 3],
    selectedTab: 'work_time',
    selectedGroupId: '',
    trainingsMap: {},
    technicalIssuesMap: {},
    offlineActivitiesMap: {},
    tezSuccessMap: {},
    ...overrides,
});

test('отработанные часы дня: база плюс засчитанные тренинги, техсбои и офлайн — как в клетке', () => {
    const result = totals({
        filteredOperators: [
            { operator_id: 1, daily: { 1: { work_time: 7.5 } } },
            { operator_id: 2, daily: { 1: { work_time: 8 }, 2: { work_time: 4 } } },
        ],
        trainingsMap: {
            1: { 1: [
                { start_time: '10:00', end_time: '11:00', count_in_hours: true },
                { start_time: '12:00', end_time: '14:00', count_in_hours: false },
            ] },
        },
        technicalIssuesMap: { 2: { 1: [{ duration_minutes: 30 }] } },
        offlineActivitiesMap: { 2: { 1: [{ start_time: '09:00', end_time: '09:15' }] } },
    });
    assert.equal(result['1'].text, '17.25');
    assert.equal(result['2'].text, '4.00');
    assert.equal(result['3'], null, 'пустой день — пустая клетка, а не ноль');
});

test('чужой день переведённого сотрудника в итог группы не входит', () => {
    const operator = {
        operator_id: 5,
        daily: { 1: { work_time: 8 }, 2: { work_time: 6 } },
        group_segments: [
            { group_id: 10, start_day: 1, end_day: 1, is_current: false },
            { group_id: 11, start_day: 2, end_day: 31, is_current: true },
        ],
    };
    const inGroup = totals({ filteredOperators: [operator], selectedGroupId: '11' });
    assert.equal(inGroup['1'], null);
    assert.equal(inGroup['2'].text, '6.00');
    // Без выбранной группы замков нет — видно всё.
    assert.equal(totals({ filteredOperators: [operator] })['1'].text, '8.00');
});

test('звонки, чаты и успешки — целые штуки с разрядами', () => {
    const operators = [
        { operator_id: 1, daily: { 1: { calls: 700, chats: 3 } } },
        { operator_id: 2, daily: { 1: { calls: 650, chats: 2 } } },
    ];
    assert.equal(totals({ filteredOperators: operators, selectedTab: 'calls' })['1'].text, '1 350');
    assert.equal(totals({ filteredOperators: operators, selectedTab: 'chats' })['1'].text, '5');
    const successes = totals({
        filteredOperators: operators,
        selectedTab: 'tez_successes',
        tezSuccessMap: { 1: { 1: 2 }, 2: { 1: 3, 2: 1 } },
    });
    assert.equal(successes['1'].text, '5');
    assert.equal(successes['2'].text, '1');
});

test('перерыв, время набора и разговора складываются в часы', () => {
    const operators = [
        { operator_id: 1, daily: { 1: { break_time: 0.5, dial_time: 1.25, talk_time: 2 } } },
        { operator_id: 2, daily: { 1: { break_time: 0.75, dial_time: 0.5, talk_time: 3.5 } } },
    ];
    assert.equal(totals({ filteredOperators: operators, selectedTab: 'break_time' })['1'].text, '1.25');
    assert.equal(totals({ filteredOperators: operators, selectedTab: 'dial_time' })['1'].text, '1.75');
    assert.equal(totals({ filteredOperators: operators, selectedTab: 'talk_time' })['1'].text, '5.50');
});

test('эффективность дня — сумма эффективных часов на сумму рабочих, а не среднее процентов', () => {
    const result = totals({
        selectedTab: 'efficiency',
        filteredOperators: [
            { operator_id: 1, daily: { 1: { work_time: 8, efficiency: 8 } } },
            { operator_id: 2, daily: { 1: { work_time: 2, efficiency: 0 } } },
            // Эффективность без рабочего времени в клетке — прочерк, в итог не входит.
            { operator_id: 3, daily: { 1: { work_time: 0, efficiency: 5 } } },
        ],
    });
    // Среднее процентов дало бы 50%, по суммам — 8 из 10 часов.
    assert.equal(result['1'].text, '80%');
    assert.equal(result['2'], null);
});

test('средняя оценка дня взвешена числом оценок', () => {
    const result = totals({
        selectedTab: 'avg_score',
        filteredOperators: [
            { operator_id: 1, daily: { 1: { chat_metrics: { avg_score: 5, score_sum: 50, score_count: 10 } } } },
            { operator_id: 2, daily: { 1: { chat_metrics: { avg_score: 3, score_sum: 3, score_count: 1 } } } },
        ],
    });
    assert.equal(result['1'].text, '4.82');
});

test('время ответа дня взвешено числом чатов, а без чатов — среднее по сотрудникам', () => {
    const weighted = totals({
        selectedTab: 'response_time',
        filteredOperators: [
            { operator_id: 1, daily: { 1: { chat_metrics: { avg_response_time_seconds: 30, chats_count: 90 } } } },
            { operator_id: 2, daily: { 1: { chat_metrics: { avg_response_time_seconds: 120, chats_count: 10 } } } },
        ],
    });
    assert.equal(weighted['1'].text, '39');
    assert.equal(weighted['1'].unit, ' с');

    const plain = totals({
        selectedTab: 'response_time',
        filteredOperators: [
            { operator_id: 1, daily: { 1: { chat_metrics: { avg_response_time_seconds: 30, chats_count: 90 } } } },
            { operator_id: 2, daily: { 1: { chat_metrics: { avg_response_time_seconds: 120 } } } },
        ],
    });
    assert.equal(plain['1'].text, '75');
});

test('активности дня: все тренинги, техсбои, офлайн и «без телефона»', () => {
    const base = {
        filteredOperators: [
            { operator_id: 1, daily: { 1: { no_phone_minutes: 30 } } },
            { operator_id: 2, daily: { 1: { no_phone_seconds: 5400 } } },
        ],
        trainingsMap: { 1: { 1: [{ start_time: '10:00', end_time: '11:00', count_in_hours: false }] } },
        technicalIssuesMap: { 2: { 1: [{ duration_hours: 1.5 }] } },
        offlineActivitiesMap: { 1: { 1: [{ duration_minutes: 45 }] } },
    };
    // Во вкладке «Тренинги» итог месяца — все часы, и засчитанные, и нет.
    assert.equal(totals({ ...base, selectedTab: 'trainings' })['1'].text, '1.00');
    assert.equal(totals({ ...base, selectedTab: 'technical_issues' })['1'].text, '1.50');
    assert.equal(totals({ ...base, selectedTab: 'offline_activity' })['1'].text, '0.75');
    assert.equal(totals({ ...base, selectedTab: 'no_phone' })['1'].text, '2.00');
});

test('бонусы и штрафы — деньги, у старых строк штраф одной суммой дня', () => {
    const operators = [
        { operator_id: 1, daily: { 1: { bonuses: [{ amount: 5000 }, { amount: 2500 }], fines: [{ amount: 1000 }] } } },
        { operator_id: 2, daily: { 1: { fine_amount: 2000 } } },
    ];
    assert.equal(totals({ filteredOperators: operators, selectedTab: 'bonuses' })['1'].text, '7 500 ₸');
    assert.equal(totals({ filteredOperators: operators, selectedTab: 'fines' })['1'].text, '3 000 ₸');
});

test('незнакомый показатель из реестра не суммируется наугад', () => {
    const result = totals({
        selectedTab: 'some_future_ratio',
        filteredOperators: [{ operator_id: 1, daily: { 1: { some_future_ratio: 0.5 } } }],
    });
    assert.equal(result['1'], null);
});
