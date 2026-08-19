import test from 'node:test';
import assert from 'node:assert/strict';

import {
  MISSING_ATTACHMENT,
  MISSING_CHECKS,
  answerValue,
  carryOver,
  groupCatalog,
  isAnswered,
  localVerdict,
  missingTarget,
  stepIsComplete,
  stepIsVisible,
  visibleSteps,
} from '../src/components/crm/wizardRules.js';

/* Эти тесты написаны по следам реальной поломки: шаг с вложением никогда не
 * считался пройденным, и четыре тематики из шести нельзя было отправить вовсе.
 * Серверные тесты её поймать не могли — регламент был верный, ломался переход
 * по шагам мастера. */

const SCENARIO = {
  key: 'demo',
  attachment: 'image',
  steps: [
    { key: 'iin', kind: 'iin' },
    { key: 'relogin_done', kind: 'yesno' },
    { key: 'docs_after_relogin', kind: 'yesno', depends_on: ['relogin_done', 'yes'] },
    { key: 'provider_changed', kind: 'yesno_date' },
    { key: 'other_device', kind: 'yesno', optional: true },
    { key: 'screenshot', kind: 'attachment' },
  ],
  rules: [
    { when: ['error_persists', 'no'], outcome: 'close', message: 'Сервис заработал' },
    { when: ['docs_visible', 'no'], outcome: 'switch', message: 'Другая тематика', switch_to: 'other' },
    { when: ['cache_cleared', 'no'], outcome: 'blocked', message: 'Очистите кэш' },
  ],
};

const attachmentStep = SCENARIO.steps.find((s) => s.kind === 'attachment');

test('шаг вложения проходится выбранным файлом, а не записью в ответах', () => {
  // Ровно та ошибка, что доехала до прода: файл лежит в своём состоянии,
  // а готовность считалась только по answers.
  assert.equal(
    stepIsComplete(attachmentStep, { answers: {}, attachment: null, scenario: SCENARIO }),
    false,
  );
  assert.equal(
    stepIsComplete(attachmentStep, { answers: {}, attachment: { name: 'shot.png' }, scenario: SCENARIO }),
    true,
  );
});

test('каждая тематика с обязательным вложением имеет путь дальше', () => {
  // Инвариант, а не частный случай: если шаг вложения последний и он
  // непроходим, отправить обращение нельзя в принципе.
  const last = SCENARIO.steps[SCENARIO.steps.length - 1];
  assert.equal(last.kind, 'attachment');
  assert.equal(stepIsComplete(last, { attachment: { name: 'x' }, scenario: SCENARIO }), true);
});

test('без требования к вложению шаг проходится пустым', () => {
  const free = { ...SCENARIO, attachment: 'none' };
  assert.equal(stepIsComplete(attachmentStep, { attachment: null, scenario: free }), true);
});

test('«да, и уточните» без уточнения — ещё не ответ', () => {
  const step = SCENARIO.steps.find((s) => s.kind === 'yesno_date');
  assert.equal(stepIsComplete(step, { answers: { provider_changed: { value: 'yes' } } }), false);
  assert.equal(
    stepIsComplete(step, { answers: { provider_changed: { value: 'yes', detail: '2026-07-10' } } }),
    true,
  );
  // «Нет» уточнения не требует.
  assert.equal(stepIsComplete(step, { answers: { provider_changed: { value: 'no' } } }), true);
});

test('необязательный шаг не держит оператора', () => {
  const step = SCENARIO.steps.find((s) => s.optional);
  assert.equal(stepIsComplete(step, { answers: {} }), true);
});

test('пустая строка и пробелы ответом не считаются', () => {
  assert.equal(isAnswered(''), false);
  assert.equal(isAnswered('   '), false);
  assert.equal(isAnswered('123'), true);
  assert.equal(isAnswered({ value: '' }), false);
  assert.equal(isAnswered(undefined), false);
});

test('зависимый шаг не показывается, пока условие не выполнено', () => {
  const dependent = SCENARIO.steps.find((s) => s.depends_on);
  assert.equal(stepIsVisible(dependent, { relogin_done: 'no' }), false);
  assert.equal(stepIsVisible(dependent, { relogin_done: 'yes' }), true);
  assert.equal(visibleSteps(SCENARIO, { relogin_done: 'no' }).length, SCENARIO.steps.length - 1);
});

test('порядок правил — это приоритет: «сервис заработал» побеждает блокировку', () => {
  // В ТЗ «сервис заработал» стоит первым пунктом среди причин не отправлять,
  // и он обязан выигрывать у «не выполнил проверку».
  const hit = localVerdict(SCENARIO, { error_persists: 'no', cache_cleared: 'no' });
  assert.equal(hit.outcome, 'close');
});

test('перевод в другую тематику срабатывает по своему ответу', () => {
  const hit = localVerdict(SCENARIO, { docs_visible: 'no' });
  assert.equal(hit.outcome, 'switch');
  assert.equal(hit.switch_to, 'other');
});

test('без совпадений правило не выдумывается', () => {
  assert.equal(localVerdict(SCENARIO, { docs_visible: 'yes' }), null);
  assert.equal(localVerdict({}, {}), null);
});

test('«не приложено вложение» ведёт на шаг вложения, а не в пустоту', () => {
  // Раньше поиск шёл только по ключам шагов и давал -1: мастер молчал,
  // а кнопка оставалась серой без единого объяснения.
  const target = missingTarget(SCENARIO.steps, { [MISSING_ATTACHMENT]: 'Приложите скриншот' });
  assert.equal(target.phase, 'steps');
  assert.equal(SCENARIO.steps[target.stepIndex].kind, 'attachment');
  assert.equal(target.message, 'Приложите скриншот');
});

test('«не подтверждены проверки» возвращает на экран проверок', () => {
  const target = missingTarget(SCENARIO.steps, { [MISSING_CHECKS]: 'Подтвердите проверки' });
  assert.equal(target.phase, 'checks');
  assert.equal(target.message, 'Подтвердите проверки');
});

test('обычная незаполненность ведёт на свой вопрос', () => {
  const target = missingTarget(SCENARIO.steps, { relogin_done: 'Не заполнено' });
  assert.equal(SCENARIO.steps[target.stepIndex].key, 'relogin_done');
});

test('при переходе в другую тематику общие ответы переносятся, частные — нет', () => {
  const carried = carryOver({ iin: '123456789012', period: '2026-07', park: 'Алматы',
                              docs_visible: 'no', error_text: 'ошибка' });
  assert.deepEqual(Object.keys(carried).sort(), ['iin', 'park', 'period']);
  assert.equal(carried.iin, '123456789012');
});

test('значение достаётся одинаково из строки и из объекта', () => {
  assert.equal(answerValue({ a: 'да' }, 'a'), 'да');
  assert.equal(answerValue({ a: { value: 'yes', detail: 'x' } }, 'a'), 'yes');
  assert.equal(answerValue({}, 'нет такого'), undefined);
});

/* ─── Картотека тематик по группам (задачи #183/#184) ─────────────────────── */

const CATALOG = [
  { key: 'sapar_docs_missing', queue_code: 'itaxi_sapar', queue_title: 'iTaxi Sapar', is_ready: true },
  { key: 'sapar_sign_error', queue_code: 'itaxi_sapar', queue_title: 'iTaxi Sapar', is_ready: true },
  { key: 'parcel_location', queue_code: 'parcels', queue_title: 'Посылки', is_ready: false },
];

test('тематики разложены по рабочим группам, а не одним списком', () => {
  const groups = groupCatalog(CATALOG);
  assert.deepEqual(groups.map((g) => g.code), ['itaxi_sapar', 'parcels']);
  assert.deepEqual(groups.map((g) => g.title), ['iTaxi Sapar', 'Посылки']);
  assert.deepEqual(groups[0].items.map((i) => i.key),
                   ['sapar_docs_missing', 'sapar_sign_error']);
});

test('порядок групп и тематик берётся с сервера, а не сортируется заново', () => {
  const reversed = [...CATALOG].reverse();
  const groups = groupCatalog(reversed);
  assert.deepEqual(groups.map((g) => g.code), ['parcels', 'itaxi_sapar']);
  assert.deepEqual(groups[1].items.map((i) => i.key),
                   ['sapar_sign_error', 'sapar_docs_missing']);
});

test('недоступная тематика остаётся в своей группе, а не пропадает', () => {
  const groups = groupCatalog(CATALOG);
  const parcels = groups.find((g) => g.code === 'parcels');
  assert.equal(parcels.items.length, 1);
  assert.equal(parcels.items[0].is_ready, false);
});

test('пустая картотека не роняет раскладку', () => {
  assert.deepEqual(groupCatalog(null), []);
  assert.deepEqual(groupCatalog([]), []);
});

/* ─── Третий ответ «Неизвестно» (задача #172) ─────────────────────────────── */

test('«неизвестно» — полноценный ответ, шаг считается пройденным', () => {
  const step = { key: 'provider_changed', kind: 'yesno_date', allow_unknown: true };
  assert.equal(stepIsComplete(step, { answers: { provider_changed: { value: 'unknown' } } }), true);
});

test('«да» по-прежнему требует уточнения, а «неизвестно» — нет', () => {
  const step = { key: 'provider_changed', kind: 'yesno_date', allow_unknown: true };
  assert.equal(stepIsComplete(step, { answers: { provider_changed: { value: 'yes', detail: '' } } }), false);
  assert.equal(stepIsComplete(step, { answers: { provider_changed: { value: 'yes', detail: '2026-07-01' } } }), true);
});
