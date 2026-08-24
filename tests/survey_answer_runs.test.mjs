import test from 'node:test';
import assert from 'node:assert/strict';

import { buildAnswerRuns, surveyRunLabel } from '../src/components/surveys/surveyRuns.js';

// Карточка сотрудника такая же, какой её собирает вкладка «Ответы».
const card = (operatorId, runId, iteration, fields = {}) => ({
    key: `${operatorId}_${runId}`,
    operatorId,
    repeatSurveyId: runId,
    repeatIteration: iteration,
    isCompleted: true,
    hasScore: false,
    scoreValue: null,
    row: { repeat_survey_title: 'Обратная связь' },
    ...fields,
});

const SURVEY = {
    id: 43,
    title: 'Обратная связь',
    created_at: '2026-08-21 10:00:00',
    repeat: { iteration: 10 },
};

const REPETITIONS = [
    { id: 41, title: 'Обратная связь', iteration: 8, created_at: '2026-08-06 09:00:00' },
    { id: 42, title: 'Обратная связь', iteration: 9, created_at: '2026-08-06 15:00:00' },
];

test('один запуск — выбирать не из чего', () => {
    const runs = buildAnswerRuns({ survey: SURVEY, repetitions: [], cards: [card(1, 43, 10)] });
    assert.equal(runs.length, 1);
    assert.equal(runs[0].id, 43);
    assert.equal(runs[0].assignedCount, 1);
});

test('свежий запуск сверху, а открытый опрос — в списке наравне с остальными', () => {
    const runs = buildAnswerRuns({ survey: SURVEY, repetitions: REPETITIONS, cards: [] });
    assert.deepEqual(runs.map((run) => run.id), [43, 42, 41]);
    assert.deepEqual(runs.map((run) => run.iteration), [10, 9, 8]);
    assert.equal(runs[2].createdAt, '2026-08-06 09:00:00');
});

test('карточки расходятся по своим запускам и ни одна не теряется', () => {
    const cards = [
        card(1, 41, 8),
        card(2, 41, 8, { isCompleted: false }),
        card(3, 42, 9),
        card(4, 43, 10),
    ];
    const runs = buildAnswerRuns({ survey: SURVEY, repetitions: REPETITIONS, cards });
    const byId = new Map(runs.map((run) => [run.id, run]));

    assert.equal(byId.get(41).assignedCount, 2);
    assert.equal(byId.get(41).completedCount, 1);
    assert.equal(byId.get(42).assignedCount, 1);
    assert.equal(byId.get(43).assignedCount, 1);
    assert.equal(runs.reduce((sum, run) => sum + run.cards.length, 0), cards.length);
});

test('запуск без назначений виден пустым, а не пропадает', () => {
    const runs = buildAnswerRuns({ survey: SURVEY, repetitions: REPETITIONS, cards: [card(1, 43, 10)] });
    const empty = runs.find((run) => run.id === 41);
    assert.equal(empty.assignedCount, 0);
    assert.equal(empty.completedCount, 0);
    assert.equal(empty.averageScore, null);
});

test('прогон, которого нет в списке повторений, заводится по самой строке ответа', () => {
    const runs = buildAnswerRuns({
        survey: SURVEY,
        repetitions: [],
        cards: [card(1, 43, 10), card(2, 34, 6, { row: { repeat_survey_title: 'Старое название' } })],
    });
    const orphan = runs.find((run) => run.id === 34);
    assert.ok(orphan, 'ответы исчезнувшего прогона должны остаться видимыми');
    assert.equal(orphan.title, 'Старое название');
    assert.equal(orphan.assignedCount, 1);
});

test('средний результат теста — только по прошедшим с баллом', () => {
    const cards = [
        card(1, 36, 1, { hasScore: true, scoreValue: 90 }),
        card(2, 36, 1, { hasScore: true, scoreValue: 70 }),
        card(3, 36, 1, { isCompleted: false, hasScore: false, scoreValue: null }),
    ];
    const runs = buildAnswerRuns({ survey: { id: 36, repeat: { iteration: 1 } }, repetitions: [], cards });
    assert.equal(runs[0].averageScore, 80);
    assert.equal(runs[0].completedCount, 2);
    assert.equal(runs[0].assignedCount, 3);
});

test('подпись запуска отсчитывается от первого', () => {
    assert.equal(surveyRunLabel(1), 'Первый запуск');
    assert.equal(surveyRunLabel(), 'Первый запуск');
    assert.equal(surveyRunLabel(7), 'Повторение #7');
});
