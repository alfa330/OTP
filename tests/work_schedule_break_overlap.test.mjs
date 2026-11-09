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

const basics = slice(APP, 'const cpad = (n) => String(n).padStart(2, "0");', '// Время рабочего часового пояса, а не браузера');
const engine = slice(APP, 'function buildOccupiedIntervalsForDate(', 'function mergeSegments(');

const { adjustBreaksForOperatorOnDate, breakLayoutScore } = new Function(`
    ${basics}
    ${engine}
    return { adjustBreaksForOperatorOnDate, breakLayoutScore };
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

test('совпавшие перерывы направления с галочкой не считаются нарушением прохода', () => {
    const sameTime = [{ start: 600 + 120, end: 600 + 150 }];
    const ops = [1, 2, 3].map(id => operator(id, 'Чат', sameTime));
    const jobs = ops.map(op => ({ op, date: DATE }));
    const [withoutFlag] = breakLayoutScore(jobs, ops, perDirection, noGap);
    const [withFlag] = breakLayoutScore(jobs, ops, perDirection, noGap, chatAllowsOverlap);
    assert.ok(withoutFlag > 0, 'трое на перерыве разом — нарушение');
    assert.equal(withFlag, 0);
});
