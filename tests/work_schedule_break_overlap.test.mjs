import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/*
 * «Разрешить пересечение» в настройке перерывов (09.11.2026).
 *
 * Расстановка перерывов живёт в трёх местах: сервер, импорт Excel и симуляция
 * планировщика в App.jsx. Здесь — третья: тест вынимает из исходника настоящие
 * функции раскладки и гоняет их на своих данных. Серверную и импортную половины
 * стережёт tests/test_work_schedule_break_rules.py (BreakOverlapAllowedTests).
 *
 * Что стережётся: с галочкой у направления перерывы коллег ЭТОГО ЖЕ направления
 * оператору не мешают — смена раскладывается так, будто коллег нет. Другое
 * направление, сведённое с ним в «Группу для перерывов», мешает как раньше.
 */

const APP = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');

function slice(source, start, end) {
    const from = source.indexOf(start);
    assert.notEqual(from, -1, `нет начала: ${start}`);
    const to = source.indexOf(end, from + start.length);
    assert.notEqual(to, -1, `нет конца: ${end}`);
    return source.slice(from, to);
}

// Даты, правила перерывов и автогенерация (с «временем перерыва»), затем раскладка.
const basics = slice(APP, 'const cpad = (n) => String(n).padStart(2, "0");', 'const intervalsOverlap = (a, b) =>');
const engine = slice(APP, 'function buildOccupiedIntervalsForDate(', 'function mergeSegments(');

const {
    adjustBreaksForOperatorOnDate,
    breakLayoutScore,
    computeBreaksForShiftMinutes,
    planBreaksForShiftWithFrozen,
} = new Function(`
    ${basics}
    ${engine}
    return { adjustBreaksForOperatorOnDate, breakLayoutScore, computeBreaksForShiftMinutes, planBreaksForShiftWithFrozen };
`)();

const DATE = '2026-11-10';
// Смена 10:00–21:00, норма 15/30/15.
const SEED = [
    { start: 760, end: 775 },
    { start: 915, end: 945 },
    { start: 1090, end: 1105 },
];
const operator = (id, direction, breaks) => ({
    id,
    direction,
    shifts: {
        [DATE]: [{
            start: '10:00',
            end: '21:00',
            __startMin: 600,
            __endMin: 1260,
            breaks: breaks.map(b => ({ ...b })),
        }],
    },
});
const perDirection = (value) => `dir:${String(value ?? '').trim().toLowerCase()}`;
const sameGroup = () => 'group:линия|чат';
const noGap = () => 0;
const chatAllowsOverlap = (value) => String(value ?? '').trim().toLowerCase() === 'чат';

function layout(others, { scope = perDirection, overlapAllowed = null } = {}) {
    const target = operator(1, 'Чат', SEED);
    adjustBreaksForOperatorOnDate(target, DATE, [target, ...others], scope, null, noGap, null, null, overlapAllowed);
    return target.shifts[DATE][0].breaks.map(b => ({ start: b.start, end: b.end }));
}

const free = layout([]);
// Трое коллег уходят на перерыв ровно тогда же, когда и он сам.
const crowd = free.flatMap(b => [0, 1, 2].map(() => ({ ...b })));
const colleagues = (direction, firstId) => crowd.map((b, i) => operator(firstId + i, direction, [b]));

test('без галочки толпа коллег своего направления сдвигает перерывы', () => {
    assert.notDeepEqual(layout(colleagues('Чат', 100)), free);
});

test('с галочкой коллеги своего направления не мешают — раскладка как без них', () => {
    assert.deepEqual(layout(colleagues('Чат', 100), { overlapAllowed: chatAllowsOverlap }), free);
    // Написание направления у коллег может отличаться пробелами и регистром.
    assert.deepEqual(layout(colleagues(' чат ', 100), { overlapAllowed: chatAllowsOverlap }), free);
});

test('галочка у другого направления не снимает правило с этого', () => {
    const lineAllows = (value) => String(value ?? '').trim().toLowerCase() === 'линия';
    assert.notDeepEqual(layout(colleagues('Чат', 100), { overlapAllowed: lineAllows }), free);
});

test('в группе перерывов чужое направление мешает как раньше, своё — нет', () => {
    assert.notDeepEqual(
        layout(colleagues('Линия', 200), { scope: sameGroup, overlapAllowed: chatAllowsOverlap }),
        free,
    );
    assert.deepEqual(
        layout(colleagues('Чат', 100), { scope: sameGroup, overlapAllowed: chatAllowsOverlap }),
        free,
    );
});

// Обед, поставленный руками (у регионов — с 13 до 14), в смене 08:00–17:00.
// Равномерная точка этой смены — 12:00, туда его и утаскивала раскладка.
const HAND_LUNCH = [{ start: 13 * 60, end: 14 * 60 }];
const lunchShift = (breaks) => ({
    id: 1,
    direction: 'Чат',
    shifts: { [DATE]: [{ start: '08:00', end: '17:00', __startMin: 480, __endMin: 1020, breaks: breaks.map(b => ({ ...b })) }] },
});
function lunchLayout(breaks, { overlapAllowed = chatAllowsOverlap, keepPlaced = true } = {}) {
    const op = lunchShift(breaks);
    adjustBreaksForOperatorOnDate(op, DATE, [op], perDirection, null, noGap, null, null, overlapAllowed, keepPlaced);
    return op.shifts[DATE][0].breaks.map(b => ({ start: b.start, end: b.end }));
}

test('с галочкой поставленный руками перерыв остаётся там, где его поставили', () => {
    assert.deepEqual(lunchLayout(HAND_LUNCH), HAND_LUNCH);
    // Не ручной (сгенерированный) — по-прежнему встаёт равномерно.
    assert.deepEqual(lunchLayout(HAND_LUNCH, { keepPlaced: false }), [{ start: 12 * 60, end: 13 * 60 }]);
});

test('без галочки ручной перерыв, как и раньше, подстраивается под правила', () => {
    assert.deepEqual(lunchLayout(HAND_LUNCH, { overlapAllowed: () => false }), [{ start: 12 * 60, end: 13 * 60 }]);
});

test('ручной перерыв, который не помещается в смену, раскладывается обычным порядком', () => {
    assert.deepEqual(lunchLayout([{ start: 16 * 60 + 30, end: 17 * 60 + 30 }]), [{ start: 12 * 60, end: 13 * 60 }]);
});

// «Время перерыва» направления едет вместе с его правилами: поле fixedBreak у массива.
const LUNCH = { start: 13 * 60, end: 14 * 60 };
const withFixed = (rules, fixed = LUNCH) => Object.assign(rules.slice(), { fixedBreak: { ...fixed } });

test('время перерыва ставится сменам, которые его покрывают, остальным — по правилам', () => {
    assert.deepEqual(computeBreaksForShiftMinutes(9 * 60, 18 * 60, 'Регионы', withFixed([])), [LUNCH]);
    assert.deepEqual(
        computeBreaksForShiftMinutes(14 * 60, 23 * 60, 'Регионы', withFixed([])),
        computeBreaksForShiftMinutes(14 * 60, 23 * 60, 'Регионы', []),
    );
    // Окно смены предлагает то же время (будущая дата — прошлого нет).
    const plan = planBreaksForShiftWithFrozen(9 * 60, 18 * 60, 'Регионы', withFixed([]), [], '2099-01-05');
    assert.deepEqual(plan.breaks, [LUNCH]);
    assert.deepEqual(plan.frozen, []);
});

test('раскладка не двигает время перерыва направления с галочкой', () => {
    const rules = [{ minMinutes: 300, maxMinutes: 720, breakDurations: [60] }];
    const layout = (ranges, overlapAllowed) => {
        // Смена 08:00–17:00 без перерывов: их ставит автогенерация.
        const op = { id: 1, direction: 'Регионы', shifts: { [DATE]: [{ start: '08:00', end: '17:00', __startMin: 480, __endMin: 1020 }] } };
        adjustBreaksForOperatorOnDate(op, DATE, [op], perDirection, () => ranges, noGap, null, null, overlapAllowed);
        return op.shifts[DATE][0].breaks.map(b => ({ start: b.start, end: b.end }));
    };
    assert.deepEqual(layout(withFixed(rules), () => true), [LUNCH]);
    // Без времени — равномерная точка 12:00.
    assert.deepEqual(layout(rules, () => true), [{ start: 12 * 60, end: 13 * 60 }]);
    // Без галочки время не держится (в жизни оно и не приезжает без неё).
    assert.deepEqual(layout(withFixed(rules), () => false), [{ start: 12 * 60, end: 13 * 60 }]);
});

test('совпавшие перерывы направления с галочкой не считаются нарушением прохода', () => {
    const sameTime = [{ start: 600 + 120, end: 600 + 150 }];
    const ops = [1, 2, 3].map(id => operator(id, 'Чат', sameTime));
    const jobs = ops.map(op => ({ op, date: DATE }));
    const [withoutFlag] = breakLayoutScore(jobs, ops, perDirection, noGap);
    const [withFlag] = breakLayoutScore(jobs, ops, perDirection, noGap, chatAllowsOverlap);
    assert.ok(withoutFlag > 0, 'трое на перерыве разом — нарушение');
    assert.equal(withFlag, 0);
});
