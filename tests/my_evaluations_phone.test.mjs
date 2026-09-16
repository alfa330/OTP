/*
 * Правила телефонного вида «Моих оценок»: что за оценка, какого цвета балл,
 * что показать по критериям и можно ли просить переоценку.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildEvaluationCriteria,
  buildEvaluationRows,
  buildEvaluationTest,
  canRequestReevaluation,
  describeEvaluationSubject,
  evaluationIsImported,
  evaluationScoreTone,
  evaluationVerdict,
  formatEvaluationDay,
  formatEvaluationScore,
  formatEvaluationsCount,
  summarizeEvaluationCriteria,
  summarizeEvaluationKinds,
  summarizeMonitoringScale,
} from '../src/components/evaluation/myEvaluationsPhone.js';

test('тон балла повторяет пороги getScoreColor: 90 и 60', () => {
  assert.equal(evaluationScoreTone(100), 'green');
  assert.equal(evaluationScoreTone(90), 'green');
  assert.equal(evaluationScoreTone(89.9), 'amber');
  assert.equal(evaluationScoreTone(60), 'amber');
  assert.equal(evaluationScoreTone(59), 'red');
  // Пустой балл и ноль серые — ровно как на компьютере.
  assert.equal(evaluationScoreTone(null), 'slate');
  assert.equal(evaluationScoreTone(0), 'slate');
  assert.equal(evaluationScoreTone('мусор'), 'slate');
});

test('балл в строке — целым числом, пустой — прочерком', () => {
  assert.equal(formatEvaluationScore(87.4), '87');
  assert.equal(formatEvaluationScore('92'), '92');
  assert.equal(formatEvaluationScore(null), '—');
  assert.equal(formatEvaluationScore(''), '—');
});

test('срок проверки — день без времени', () => {
  assert.equal(formatEvaluationDay('2026-09-19'), '19 сент. 2026');
  assert.equal(formatEvaluationDay('2026-01-05T00:00:00'), '05 янв. 2026');
  assert.equal(formatEvaluationDay(''), '');
});

test('что оценено: тест, чат или звонок', () => {
  assert.deepEqual(
    describeEvaluationSubject({ knowledge_test: { title: 'Тариф' } }),
    { kind: 'test', title: 'Тариф', typeLabel: 'Тестирование знаний' },
  );
  assert.equal(describeEvaluationSubject({ c2d_snapshot_id: 12, phone_number: '+7700' }).kind, 'chat');
  assert.equal(describeEvaluationSubject({ phone_number: '+7700' }).kind, 'call');
  assert.equal(describeEvaluationSubject({}).title, '—');
});

test('импортированный звонок не оценён — просить переоценку нечего', () => {
  const imported = { call: { is_imported: true }, score: 50 };
  assert.equal(evaluationIsImported(imported), true);
  assert.equal(canRequestReevaluation(imported, 'none'), false);
  assert.equal(canRequestReevaluation({ score: 50 }, 'none'), true);
  assert.equal(canRequestReevaluation({ score: 100 }, 'none'), false, 'сотню оспаривать нечего');
  assert.equal(canRequestReevaluation({ score: 50 }, 'pending'), false);
  assert.equal(canRequestReevaluation({ score: 50 }, 'approved'), false);
  assert.equal(canRequestReevaluation({ score: 50 }, 'rejected'), true, 'отклонённый можно повторить');
});

test('критерии берутся из шкалы направления, иначе — из голых статусов', () => {
  const withDirection = buildEvaluationCriteria({
    direction: { criteria: [{ name: 'Приветствие', weight: 20, isCritical: false }, { name: 'Грубость', isCritical: true }] },
    scores: ['Correct', 'Incorrect'],
    criterion_comments: ['', 'перебил клиента'],
  });
  assert.deepEqual(withDirection.map((item) => [item.number, item.name, item.status, item.weight, item.isCritical, item.comment]), [
    [1, 'Приветствие', 'Correct', 20, false, ''],
    [2, 'Грубость', 'Incorrect', null, true, 'перебил клиента'],
  ]);

  const legacy = buildEvaluationCriteria({ scores: ['Correct', 'N/A'], criterion_names: ['Первый'] });
  assert.deepEqual(legacy.map((item) => item.name), ['Первый', 'Критерий 2']);
  assert.deepEqual(buildEvaluationCriteria({}), []);
});

test('вердикты названы теми же словами, что у проверяющих', () => {
  assert.equal(evaluationVerdict('Correct').label, 'Верно');
  assert.equal(evaluationVerdict('Incorrect').label, 'Неверно');
  assert.equal(evaluationVerdict('Deficiency').label, 'Недочёт');
  assert.equal(evaluationVerdict('N/A').tone, 'slate');
  assert.equal(evaluationVerdict(null).label, '—');
});

test('свод по критериям идёт постоянным порядком и без пустых статусов', () => {
  const summary = summarizeEvaluationCriteria([
    { status: 'Incorrect' }, { status: 'Correct' }, { status: 'Correct' }, { status: null },
  ]);
  assert.deepEqual(summary.map((item) => [item.label, item.count]), [['Верно', 2], ['Неверно', 1]]);
});

test('карточка теста: баллы за вопросы и результат в процентах', () => {
  const test1 = buildEvaluationTest({ score: 83.0, knowledge_test: { title: 'Тариф', earned_points: 5, max_points: 6 } });
  assert.equal(test1.points, '5 / 6');
  assert.equal(test1.result, '83%');
  assert.equal(test1.autoSubmitted, false);
  assert.equal(buildEvaluationTest({ score: 10 }), null);
  assert.equal(buildEvaluationTest({ knowledge_test: {} }).points, '—');
});

test('строки списка нумеруются и несут тон балла', () => {
  const rows = buildEvaluationRows([
    { id: 7, score: 95, phone_number: '+7700' },
    { score: 40, c2d_snapshot_id: 3 },
  ]);
  assert.deepEqual(rows.map((row) => [row.id, row.number, row.kind, row.tone]), [
    [7, 1, 'call', 'green'],
    [1, 2, 'chat', 'red'],
  ]);
});

test('ключ строки разводит оценку и неоценённый звонок с тем же id', () => {
  const rows = buildEvaluationRows([
    { id: 1, is_imported: true, phone_number: '+7700' },
    { id: 1, score: 92, phone_number: '+7701' },
  ]);
  assert.deepEqual(rows.map((row) => row.key), ['imported:1', 'call:1']);
  assert.equal(new Set(rows.map((row) => row.key)).size, 2);
});

test('виды оценок называются, только когда их больше одного', () => {
  assert.deepEqual(summarizeEvaluationKinds([{ phone_number: '1' }, { phone_number: '2' }]), []);
  assert.deepEqual(
    summarizeEvaluationKinds([{ phone_number: '1' }, { c2d_snapshot_id: 2 }, { knowledge_test: {} }]).map((k) => [k.label, k.count]),
    [['Звонки', 1], ['Чаты', 1], ['Тесты', 1]],
  );
});

test('счётный падеж у числа оценок', () => {
  assert.equal(formatEvaluationsCount(1), '1 оценка');
  assert.equal(formatEvaluationsCount(3), '3 оценки');
  assert.equal(formatEvaluationsCount(11), '11 оценок');
  assert.equal(formatEvaluationsCount(21), '21 оценка');
  assert.equal(formatEvaluationsCount(0), '0 оценок');
});

test('сводка шкалы: критичные в вес не входят', () => {
  assert.deepEqual(
    summarizeMonitoringScale([{ weight: 30 }, { weight: 70 }, { isCritical: true, weight: 50 }]),
    { total: 3, critical: 1, weight: 100 },
  );
  assert.deepEqual(summarizeMonitoringScale(), { total: 0, critical: 0, weight: 0 });
});
