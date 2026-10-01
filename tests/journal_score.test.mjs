import test from 'node:test';
import assert from 'node:assert/strict';

import { criticalDeficiencyPenalty, journalTotalScore } from '../src/call_evaluation/journalScore.js';

/**
 * Итог «Журнала оценок» — одна формула на окна звонка, чата и калибровки.
 * Шкала здесь в форме directions.criteria (camelCase), как её читает журнал.
 */

const SCALE = [
    { name: 'Приветствие', weight: 60, isCritical: false },
    { name: 'Выявление', weight: 40, isCritical: false, deficiency: { weight: 15, description: 'частично' } },
    { name: 'Грубость', weight: 0, isCritical: true, deficiency: { weight: 10, description: 'повышенный тон' } },
    { name: 'Обман', weight: 0, isCritical: true, deficiency: null },
];

test('взвешенные критерии: полный вес, вес недочёта или ноль', () => {
    assert.equal(journalTotalScore(SCALE, ['Correct', 'Correct', 'Correct', 'Correct']), 100);
    assert.equal(journalTotalScore(SCALE, ['N/A', 'Deficiency', 'Correct', 'Correct']), 75);
    assert.equal(journalTotalScore(SCALE, ['Incorrect', 'Correct', 'Correct', 'Correct']), 40);
});

test('критическая ошибка обнуляет итог', () => {
    assert.equal(journalTotalScore(SCALE, ['Correct', 'Correct', 'Correct', 'Error']), 0);
    assert.equal(journalTotalScore(SCALE, ['Correct', 'Correct', 'Error', 'Correct']), 0);
});

test('недочёт критического критерия снимает установленные баллы, не ниже нуля', () => {
    assert.equal(criticalDeficiencyPenalty(SCALE[2]), 10);
    assert.equal(criticalDeficiencyPenalty(SCALE[1]), 0);
    assert.equal(criticalDeficiencyPenalty(SCALE[3]), 0);
    assert.equal(journalTotalScore(SCALE, ['Correct', 'Correct', 'Deficiency', 'Correct']), 90);
    assert.equal(journalTotalScore(SCALE, ['Correct', 'Deficiency', 'Deficiency', 'Correct']), 65);
    assert.equal(journalTotalScore(SCALE, ['Incorrect', 'Incorrect', 'Deficiency', 'Correct']), 0);
    assert.equal(journalTotalScore(SCALE, ['Correct', 'Correct', 'Deficiency', 'Error']), 0);
});

test('после вычета итог округляется до сотых — без хвоста двоичной дроби', () => {
    const scale = [
        { weight: 33.3, isCritical: false },
        { weight: 33.3, isCritical: false },
        { weight: 33.4, isCritical: false },
        { weight: 0, isCritical: true, deficiency: { weight: 10 } },
    ];
    // 33,3 + 33,3 − 10 в двоичной дроби — 56.599999999999994.
    assert.equal(journalTotalScore(scale, ['Correct', 'Correct', 'Incorrect', 'Deficiency']), 56.6);
});

/* Прежняя формула из окон журнала — без недочёта у критических критериев новая
 * обязана давать ровно то же число (в том числе дробное), иначе поменялись бы
 * баллы оценок, которых правка не касается. */
const legacyTotal = (criteria, scores) => {
    const hasCriticalError = criteria.some((c, i) => c?.isCritical && scores[i] === 'Error');
    return hasCriticalError ? 0 : criteria.reduce((sum, c, i) => {
        if (c?.isCritical) return sum;
        if (scores[i] === 'Correct' || scores[i] === 'N/A') return sum + (Number(c?.weight) || 0);
        if (scores[i] === 'Deficiency' && c?.deficiency?.weight != null) return sum + (Number(c?.deficiency?.weight) || 0);
        return sum;
    }, 0);
};

test('без недочёта у критических формула совпадает с прежней', () => {
    const verdicts = ['Correct', 'N/A', 'Incorrect', 'Deficiency', 'Error'];
    const scale = [
        { weight: 33.3, isCritical: false },
        { weight: 33.3, isCritical: false, deficiency: { weight: 10.5 } },
        { weight: 33.4, isCritical: false },
        { weight: 0, isCritical: true },
    ];
    for (let n = 0; n < verdicts.length ** scale.length; n += 1) {
        const scores = scale.map((_, i) => verdicts[Math.floor(n / verdicts.length ** i) % verdicts.length]);
        assert.equal(journalTotalScore(scale, scores), legacyTotal(scale, scores), scores.join(','));
    }
});
