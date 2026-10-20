// «Дисциплина» (#254): клиентские правила отрисовки — ступени, тоны, шапки групп.
// Запуск: node --test tests/discipline_rules.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    daysLeftText, fmtDateTime, fmtMoney, groupSummary, groupTone, hasTimecode, isFullMoment, isOvernight, joinMoment,
    joinShift, joinTimecodes, momentProblem, ordinalWord, pluralViolations, shiftParts, shiftProblem, splitMoment,
    stepChip, stepTitle, timecodeRows, timecodesProblem,
} from '../src/components/discipline/disciplineRules.js';

test('подписи ступеней — как на скриншоте заказчика', () => {
    assert.equal(stepTitle('training'), 'Тренинг');
    assert.equal(stepTitle('explanation', 2), 'Объясн. №2');
    assert.equal(stepTitle('act', 3), 'Объясн. №3 + акт');
});

test('тон чипа ступени несёт смысл', () => {
    assert.deepEqual(stepChip({ action: 'training', state: 'training_required' }), { label: 'Провести тренинг', tone: 'rose' });
    assert.deepEqual(stepChip({ action: 'training', state: 'training_done' }), { label: 'Тренинг', tone: 'green' });
    assert.equal(stepChip({ action: 'explanation', state: 'explanation_requested', step_title: 'Объясн. №1' }).tone, 'amber');
    assert.equal(stepChip({ action: 'explanation', state: 'explanation_received', step_title: 'Объясн. №1' }).tone, 'green');
    assert.deepEqual(stepChip({ action: 'explanation', state: 'explanation_refused' }), { label: 'Отказ от объяснения', tone: 'amber' });
    assert.equal(stepChip({ action: 'act', state: 'act_done', step_title: 'Объясн. №3 + акт' }).tone, 'red');
    assert.deepEqual(stepChip({ action: 'act', state: 'cancelled' }), { label: 'Отменено', tone: 'slate' });
});

test('шапка группы «по сотрудникам»', () => {
    const act = [{ action: 'act', state: 'act_done', act: { has_file: true }, weighted: 4, act_step: 4 }];
    assert.equal(groupTone(act), 'red');
    assert.equal(groupSummary(act), '1 нарушение · акт составлен');
    const pre = [{ action: 'explanation', state: 'explanation_received', weighted: 3, act_step: 4, explanation_no: 2 }];
    assert.equal(groupTone(pre), 'yellow');
    assert.equal(groupSummary(pre), '1 нарушение · следующее — акт');
    // Как у Дмитрия на скриншоте: тренинг и объяснительная №1 — жёлтая шапка.
    const dmitry = [{ action: 'explanation', state: 'explanation_requested', weighted: 2, act_step: 4, explanation_no: 1 },
        { action: 'training', state: 'training_done', weighted: 1, act_step: 4 }];
    assert.equal(groupTone(dmitry), 'yellow');
    assert.equal(groupSummary(dmitry), '2 нарушения · следующее — объясн. №2');
    const fresh = [{ action: 'training', state: 'training_required', weighted: 1, act_step: 4 }];
    assert.equal(groupTone(fresh), 'normal');
    assert.equal(groupSummary(fresh), '1 нарушение · тренинг не проведён');
    // Отменённые в счёт не идут.
    assert.equal(groupSummary([{ action: 'act', state: 'cancelled', weighted: 4, act_step: 4 }]), '0 нарушений');
});

test('слова и числа', () => {
    assert.deepEqual([1, 2, 5, 11, 21, 22].map(pluralViolations),
        ['нарушение', 'нарушения', 'нарушений', 'нарушений', 'нарушение', 'нарушения']);
    assert.equal(ordinalWord(4), 'четвёртое');
    assert.equal(ordinalWord(12), '12-е');
    assert.equal(fmtDateTime('2026-10-20T09:05:00'), '20.10.2026 09:05');
    assert.equal(fmtMoney(15000), '15\u00a0000\u00a0₸');
    assert.equal(daysLeftText(0), 'заканчивается сегодня');
    assert.equal(daysLeftText(-2), 'просрочено на 2 дн.');
});

test('дата и время нарушения: половина набранного не теряется, будущее — ошибка', () => {
    assert.deepEqual(splitMoment('2026-10-20T14:30'), { date: '2026-10-20', time: '14:30' });
    assert.deepEqual(splitMoment('2026-10-20T'), { date: '2026-10-20', time: '' });
    assert.deepEqual(splitMoment(''), { date: '', time: '' });
    assert.equal(joinMoment('2026-10-20', '14:30'), '2026-10-20T14:30');
    assert.equal(joinMoment('2026-10-20', ''), '2026-10-20T');
    assert.equal(joinMoment('', ''), '');
    assert.equal(isFullMoment('2026-10-20T'), false);
    const now = new Date(2026, 9, 20, 16, 0);
    assert.equal(momentProblem('2026-10-20T15:59', now), null);
    assert.equal(momentProblem('2026-10-20T16:04', now), null);   // часы устройства и сервера расходятся
    assert.equal(momentProblem('2026-10-20T16:30', now), 'Это время ещё не наступило');
    assert.equal(momentProblem('2026-10-20T', now), 'Укажите дату и время');
});

test('смена — только время начала и конца', () => {
    assert.deepEqual(shiftParts('09:00–18:00'), { start: '09:00', end: '18:00', other: '' });
    assert.deepEqual(shiftParts('9:00-18:00'), { start: '09:00', end: '18:00', other: '' });
    assert.deepEqual(shiftParts('09:00–'), { start: '09:00', end: '', other: '' });
    assert.deepEqual(shiftParts('Линия 2'), { start: '', end: '', other: 'Линия 2' });
    assert.deepEqual(shiftParts('25:00–18:00'), { start: '', end: '', other: '25:00–18:00' });
    assert.equal(joinShift('09:00', '18:00'), '09:00–18:00');
    assert.equal(joinShift('09:00', ''), '09:00–');
    assert.equal(joinShift('', ''), '');
    assert.equal(isOvernight('20:00', '08:00'), true);
    assert.equal(isOvernight('09:00', '18:00'), false);
    assert.equal(shiftProblem('20:00–08:00'), null);
    assert.equal(shiftProblem('09:00–'), 'Укажите конец смены');
    assert.equal(shiftProblem('–18:00'), 'Укажите начало смены');
    assert.equal(shiftProblem('09:00–09:00'), 'Начало и конец смены совпадают');
    assert.equal(shiftProblem('1234'), 'Смена — время начала и конца, например 09:00–18:00');
});

test('тайм-код записи экрана — фрагменты «с … по …»', () => {
    assert.deepEqual(timecodeRows('14:32–14:41, 15:05').rows, [{ start: '14:32', end: '14:41' }, { start: '15:05', end: '' }]);
    assert.deepEqual(timecodeRows('видео 10:15'), { rows: [], other: 'видео 10:15' });
    assert.equal(joinTimecodes([{ start: '14:32', end: '14:41' }, { start: '', end: '' }, { start: '15:05', end: '15:05' }]),
        '14:32–14:41, 15:05');
    assert.equal(joinTimecodes([{ start: '', end: '14:41' }]), '–14:41');
    assert.equal(hasTimecode('14:32'), true);
    assert.equal(hasTimecode(''), false);
    assert.equal(hasTimecode('–14:41'), false);
    assert.equal(timecodesProblem('14:32–14:41'), null);
    assert.equal(timecodesProblem('–14:41'), 'Укажите, с какого времени смотреть запись');
    assert.equal(timecodesProblem('видео 10:15'), 'Тайм-код — время на записи экрана, например 14:32–14:41');
    assert.equal(timecodesProblem('10:00, 10:01, 10:02, 10:03, 10:04, 10:05'), 'Не больше 5 фрагментов записи');
});
