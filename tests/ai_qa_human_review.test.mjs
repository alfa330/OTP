import test from 'node:test';
import assert from 'node:assert/strict';

import {
    allowedVerdicts, verdictFromAi, scoreOf, validateReview, isEmptyReview, validationLabel,
    alignScores, alignComments, filledCount, isComplete,
    toAiVerdict, aiVerdictOf, isCorrection, aiCommentFor, initialVerdicts, validateCorrections,
    quoteInTranscript, criticalDeficiencyPenalty,
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

/* Недочёт критического критерия — вычет установленного числа баллов из итога
 * вместо обнуления (как в журнале и на сервере: call_qa/human_review.score_of). */
test('недочёт критического критерия снимает баллы, а не обнуляет', () => {
    const scale = [
        crit(0, 60),
        crit(1, 40, { deficiency: { weight: 15, description: 'частично' } }),
        crit(2, null, { is_critical: true, deficiency: { weight: 10, description: 'тон' } }),
        crit(3, null, { is_critical: true }),
    ];
    assert.deepEqual(allowedVerdicts(scale[2]), ['Correct', 'N/A', 'Deficiency', 'Error']);
    assert.deepEqual(allowedVerdicts(scale[3]), ['Correct', 'N/A', 'Error']);
    assert.equal(criticalDeficiencyPenalty(scale[2]), 10);
    assert.equal(criticalDeficiencyPenalty(scale[1]), 0);
    assert.equal(verdictFromAi(scale[2], 'Deficiency'), 'Deficiency');
    assert.equal(verdictFromAi(scale[2], 'Incorrect'), 'Error');
    assert.equal(scoreOf(scale, ['Correct', 'Correct', 'Deficiency', 'Correct']), 90);
    assert.equal(scoreOf(scale, ['Correct', 'Deficiency', 'Deficiency', 'Correct']), 65);
    assert.equal(scoreOf(scale, ['Incorrect', 'Incorrect', 'Deficiency', 'Correct']), 0);
    assert.equal(scoreOf(scale, ['Correct', 'Correct', 'Deficiency', 'Error']), 0);
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

/* Карточка оценки одна на ИИ и человека: она открывается заполненной вердиктами
 * ИИ, согласие — ноль нажатий, правка — нажатие и комментарий. */
const T = (idx, weight, ai, extra = {}) => ({ ...crit(idx, weight, extra), source: 'transcript', ai, comment: `ИИ о ${idx}` });

test('старт из ИИ: вердикты переведены, комментарий подставлен только к ошибкам', () => {
    const scale = [
        T(0, 40, 'Correct'),
        T(1, 30, 'Deficiency', { deficiency: { weight: 15 } }),
        T(2, 30, 'Incorrect'),
        T(3, null, 'Incorrect', { is_critical: true }),
        { ...crit(4, 0), source: 'system_api', ai: 'Pending', comment: 'нужна проверка данных в ПО' },
    ];
    const { scores, comments } = initialVerdicts(scale, null);
    assert.deepEqual(scores, ['Correct', 'Deficiency', 'Incorrect', 'Error', null]);
    assert.deepEqual(comments, ['', 'ИИ о 1', 'ИИ о 2', 'ИИ о 3', '']);
    // Своя сохранённая оценка важнее ИИ.
    assert.deepEqual(initialVerdicts(scale, { scores: ['N/A'], criterion_comments: ['моё'] }).scores,
        ['N/A', null, null, null, null]);
});

test('критическая ошибка ИИ видна как «Критич. ошибка» и обнуляет итог', () => {
    const critical = T(3, null, 'Incorrect', { is_critical: true });
    assert.equal(verdictFromAi(critical, 'Incorrect'), 'Error');
    // GLM писал «Error» вне схемы — это тоже критическая ошибка, а не пустая кнопка.
    assert.equal(verdictFromAi(critical, 'Error'), 'Error');
    // По некритическому критерию «Error» старых прогонов — обычная ошибка.
    assert.equal(verdictFromAi(SCALE[0], 'Error'), 'Incorrect');
    const scale = [T(0, 100, 'Correct'), critical];
    assert.equal(scoreOf(scale, initialVerdicts(scale, null).scores), 0);
});

test('исправление — расхождение с ИИ в его словаре', () => {
    const critical = T(3, null, 'Incorrect', { is_critical: true });
    assert.equal(toAiVerdict('Error'), 'Incorrect');
    assert.equal(isCorrection(critical, 'Error'), false);           // согласие с ИИ
    assert.equal(isCorrection({ ...critical, ai: 'Error' }, 'Error'), false);
    assert.equal(isCorrection(critical, 'Correct'), true);
    assert.equal(isCorrection(T(0, 40, 'Correct'), 'Correct'), false);
    assert.equal(isCorrection(T(0, 40, 'Correct'), 'N/A'), true);
    // Что ИИ не оценивал, человек ставит сам — это не исправление ИИ.
    assert.equal(isCorrection(T(0, 40, 'Pending'), 'Incorrect'), false);
    assert.equal(isCorrection({ ...crit(4, 0), source: 'manual', ai: 'Correct' }, 'Incorrect'), false);
    assert.equal(aiVerdictOf(T(0, 40, 'Pending')), null);
    assert.equal(isCorrection(T(0, 40, 'Correct'), null), false);
});

test('комментарий ИИ — только к его же отрицательному вердикту', () => {
    const c = T(2, 30, 'Incorrect');
    assert.equal(aiCommentFor(c, 'Incorrect'), 'ИИ о 2');
    assert.equal(aiCommentFor(c, 'Correct'), '');
    assert.equal(aiCommentFor(T(0, 40, 'Correct'), 'Incorrect'), '');
});

test('исправлению нужно объяснение, а вписанная цитата обязана найтись', () => {
    const scale = [T(0, 50, 'Incorrect'), T(1, 50, 'Correct')];
    const scores = ['Correct', 'Incorrect'];
    const found = (text) => quoteInTranscript(text, 'Оператор: Здравствуйте, меня зовут Алия');
    let check = validateCorrections(scale, scores, ['', ''], {}, found);
    assert.deepEqual(check.reasonsRequired, [0, 1]);
    assert.equal(check.ok, false);
    // Правило для ИИ заменяет комментарий; цитаты, которой нет в разговоре, — нельзя.
    check = validateCorrections(scale, scores, ['', 'не уточнила вопрос'],
        { 0: { rule: 'Приветствие с именем — верно', excerpt: 'меня зовут Алия' } }, found);
    assert.equal(check.ok, true);
    check = validateCorrections(scale, scores, ['да', 'нет'], { 1: { excerpt: 'такого не было' } }, found);
    assert.deepEqual(check.quotesInvalid, [1]);
    assert.match(validationLabel({ ...check, missing: [], commentsRequired: [] }), /цитаты нет/);
});

test('предпроверка цитаты — нормализация сервера', () => {
    const text = '[26.07 21:17] Оператор (Алия): Здравствуйте!  Чем могу помочь?';
    assert.equal(quoteInTranscript('здравствуйте, чем могу помочь', text), true);
    assert.equal(quoteInTranscript('26.07 21:17 Оператор', text), true);
    assert.equal(quoteInTranscript('до свидания', text), false);
    assert.equal(quoteInTranscript('чем', text), false);   // короче 4 символов сервер не принимает
});
