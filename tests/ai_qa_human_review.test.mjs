import test from 'node:test';
import assert from 'node:assert/strict';

import {
    allowedVerdicts, verdictFromAi, scoreOf, validateReview, isEmptyReview, validationLabel,
    alignScores, alignComments, filledCount, isComplete,
} from '../src/components/call_qa/humanReview.js';

/**
 * «Моя оценка» в карточке ИИ-оценки — правила вердиктов и балл.
 *
 * Модуль зеркалит call_qa/human_review.py; сервер — истина, но балл человек
 * видит ДО отправки, и расхождение читалось бы как «карточка обманула».
 */

const crit = (idx, weight, extra = {}) => ({ idx, name: `К${idx}`, weight, is_critical: false, deficiency: null, ...extra });
const SCALE = [
    crit(0, 40),
    crit(1, 30, { deficiency: { weight: 15, description: 'частично' } }),
    crit(2, 30),
    crit(3, null, { is_critical: true }),
];

test('кнопки у критерия — как в журнале', () => {
    assert.deepEqual(allowedVerdicts(SCALE[0]), ['Correct', 'Incorrect', 'N/A']);
    assert.deepEqual(allowedVerdicts(SCALE[1]), ['Correct', 'Incorrect', 'Deficiency', 'N/A']);
    assert.deepEqual(allowedVerdicts(SCALE[3]), ['Correct', 'N/A', 'Error']);
});

test('«как у ИИ»: критический Incorrect → Error, Pending → пусто', () => {
    assert.equal(verdictFromAi(SCALE[3], 'Incorrect'), 'Error');
    assert.equal(verdictFromAi(SCALE[0], 'Incorrect'), 'Incorrect');
    assert.equal(verdictFromAi(SCALE[1], 'Deficiency'), 'Deficiency');
    assert.equal(verdictFromAi(SCALE[0], 'Deficiency'), 'Incorrect');
    assert.equal(verdictFromAi(SCALE[0], 'Pending'), null);
    assert.equal(verdictFromAi(SCALE[0], null), null);
});

test('балл — формула журнала', () => {
    assert.equal(scoreOf(SCALE, ['Correct', 'Correct', 'Correct', 'Correct']), 100);
    assert.equal(scoreOf(SCALE, ['Incorrect', 'Correct', 'Correct', 'Correct']), 60);
    assert.equal(scoreOf(SCALE, ['Correct', 'Deficiency', 'Correct', 'Correct']), 85);
    assert.equal(scoreOf(SCALE, ['Correct', 'Correct', 'N/A', 'N/A']), 100);
    assert.equal(scoreOf(SCALE, ['Correct', 'Correct', 'Correct', 'Error']), 0);
});

test('частичная оценка балла не имеет', () => {
    assert.equal(scoreOf(SCALE, ['Correct', null, 'Correct', 'Correct']), null);
    assert.equal(isComplete(SCALE, ['Correct', null, 'Correct', 'Correct']), false);
    assert.equal(filledCount(SCALE, ['Correct', null, 'Correct', '']), 2);
});

test('в журнал — только полная; комментарий к ошибке обязателен всегда', () => {
    const forJournal = validateReview(SCALE, ['Correct', null, 'Correct', null], ['', '', '', ''], { completeRequired: true });
    assert.deepEqual(forJournal.missing, [1, 3]);
    assert.equal(forJournal.ok, false);
    const calibration = validateReview(SCALE, ['Incorrect', null, null, 'Error'], ['', '', '', 'почему'], { completeRequired: false });
    assert.deepEqual(calibration.missing, []);
    assert.deepEqual(calibration.commentsRequired, [0]);
    assert.equal(calibration.ok, false);
    assert.match(validationLabel(calibration), /комментарий/);
    // Недочёт комментария не требует — как в форме журнала.
    assert.equal(validateReview(SCALE, [null, 'Deficiency', null, null], ['', '', '', '']).ok, true);
});

test('пустая оценка распознаётся', () => {
    assert.equal(isEmptyReview([null, null], ['', ''], ''), true);
    assert.equal(isEmptyReview([null, 'Correct'], ['', ''], ''), false);
    assert.equal(isEmptyReview([null, null], ['', ''], 'заметка'), false);
});

test('сохранённая оценка выравнивается по шкале карточки', () => {
    assert.deepEqual(alignScores(SCALE, ['Correct', '']), ['Correct', null, null, null]);
    assert.deepEqual(alignComments(SCALE, ['a']), ['a', '', '', '']);
    assert.deepEqual(alignScores(SCALE, undefined), [null, null, null, null]);
});
