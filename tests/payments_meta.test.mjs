import test from 'node:test';
import assert from 'node:assert/strict';

import {
  REGISTRY_COLUMNS,
  STATE_FILTERS,
  STATE_META,
  approverBasisLine,
  cardNumberLabel,
  daysUntil,
  defaultRegistryColumns,
  expenseSubline,
  describeEvent,
  dueLabel,
  fmtDate,
  fmtDateTime,
  fmtMoney,
  itemsTotal,
  netAmount,
  normalizeRegistryColumns,
  parseAmount,
  passedStepsLabel,
  pluralDays,
  priceTrend,
  requestState,
  responsibleLines,
  routeSummary,
  rowTone,
  splitRoute,
  stepLabel,
  toneRow,
  trendLabel,
} from '../src/components/payments/paymentsMeta.js';

/* Правила раздела «Оплата счетов», которые раньше жили бы в разметке.
 * Главное здесь — цвет строки только у состояний со смыслом и деньги без
 * «прыгающих» форматов. */

test('строка красится только у состояний со смыслом', () => {
  assert.equal(rowTone({ status: 'active', state: 'active' }), null);
  assert.equal(rowTone({ status: 'active', state: 'overdue' }), 'overdue');
  assert.equal(rowTone({ status: 'active', state: 'blocked' }), 'blocked');
  assert.equal(rowTone({ status: 'done', state: 'done' }), 'done');
  assert.equal(rowTone({ status: 'rejected', state: 'rejected' }), 'rejected');
  assert.equal(rowTone({ status: 'cancelled', state: 'cancelled' }), 'rejected');
  assert.equal(toneRow(null), '');
  assert.ok(toneRow('overdue').includes('rose'));
});

test('состояние берётся с сервера, а без него — из статуса', () => {
  assert.equal(requestState({ status: 'active' }), 'active');
  assert.equal(requestState({ status: 'done' }), 'done');
  assert.equal(requestState({ status: 'active', state: 'blocked' }), 'blocked');
  assert.equal(requestState(null), 'active');
});

test('фильтры легенды покрывают все состояния и «ждут меня»', () => {
  const keys = STATE_FILTERS.map((item) => item.key);
  assert.deepEqual(keys, ['all', 'mine', 'open', 'blocked', 'overdue', 'done', 'rejected']);
  for (const filter of STATE_FILTERS) {
    if (filter.tone) assert.ok(Object.values(STATE_META).some((meta) => meta.tone === filter.tone), filter.key);
  }
});

test('деньги: разбор строки с пробелами и запятой, формат с узким пробелом и ₸', () => {
  assert.equal(parseAmount('1 234,50'), 1234.5);
  assert.equal(parseAmount('300 000 ₸'), 300000);
  assert.equal(parseAmount(''), 0);
  assert.equal(parseAmount('abc'), 0);
  assert.equal(fmtMoney(1234567.5), '1 234 567,50 ₸');
  assert.equal(fmtMoney(300000), '300 000 ₸');
  assert.equal(fmtMoney(2500, { currency: false }), '2 500');
  assert.equal(fmtMoney(-15), '−15 ₸');
});

test('сумма позиций — количество × цена, округление до копейки', () => {
  const items = [
    { name: 'Бумага А4', quantity: 1, unit_price: 2500 },
    { name: 'Карандаш', quantity: 3, unit_price: 150 },
    { name: '', quantity: '', unit_price: '' },
  ];
  assert.equal(itemsTotal(items), 2950);
  assert.equal(itemsTotal([{ quantity: 3, unit_price: 0.1 }]), 0.3);
});

test('даты: dd.mm.yyyy без часового сдвига, срок словами', () => {
  assert.equal(fmtDate('2026-09-14'), '14.09.2026');
  assert.equal(fmtDateTime('2026-09-14T10:58:00'), '14.09.2026 10:58');
  assert.equal(fmtDate(null), '—');
  assert.equal(daysUntil('2026-09-20', '2026-09-14'), 6);
  assert.equal(pluralDays(1), '1 день');
  assert.equal(pluralDays(3), '3 дня');
  assert.equal(pluralDays(11), '11 дней');
  assert.equal(dueLabel({ due_on: '2026-09-20', status: 'active', current_step: 7 }, '2026-09-14'), 'через 6 дней');
  assert.equal(dueLabel({ due_on: '2026-09-10', status: 'active', current_step: 7 }, '2026-09-14'), 'просрочен на 4 дня');
  assert.equal(dueLabel({ due_on: '2026-09-14', status: 'active', current_step: 7 }, '2026-09-14'), 'сегодня');
  assert.equal(dueLabel({ due_on: '2026-09-10', status: 'active', current_step: 11 }, '2026-09-14'), 'к 10.09.2026');
});

test('подпись этапа и согласующего', () => {
  const steps = [{ no: 7, title: 'Согласование счёта на оплату' }];
  assert.equal(stepLabel({ status: 'active', current_step: 7 }, steps), 'Согласование счёта на оплату');
  assert.equal(stepLabel({ status: 'done', current_step: 12 }, steps), 'Все 12 шагов пройдены');
  assert.equal(stepLabel({ status: 'rejected', current_step: 3 }, steps), 'Отклонена на шаге 3');

  const byOrder = routeSummary({ approver_name: 'Директор по развитию', order_number: '15' });
  assert.equal(byOrder.approver, 'Директор по развитию');
  assert.ok(byOrder.byOrder);
  const standard = routeSummary({ standard_label: 'Учредитель', evaluations: [{ reason: 'Приказ №15 не применён. Причина: лимит.' }] });
  assert.equal(standard.approver, 'Учредитель');
  assert.ok(standard.basisText.includes('не применён'));
});

test('история читается словами, а не кодами', () => {
  const steps = [{ no: 7, title: 'Согласование счёта на оплату' }];
  assert.equal(describeEvent({ kind: 'step_done', step_no: 7 }, steps), 'Шаг 7 «Согласование счёта на оплату» — отписка');
  assert.equal(describeEvent({ kind: 'returned', step_no: 7 }, steps), 'Возвращена на шаг 7 «Согласование счёта на оплату»');
  assert.equal(describeEvent({ kind: 'edited', payload: { changes: { amount: [1, 2], notes: ['', 'x'] } } }), 'Изменено: Сумма, Примечания');
  assert.equal(describeEvent({ kind: 'attachment_added', payload: { file_name: 'счёт.pdf' } }), 'Файл добавлен: счёт.pdf');
  assert.equal(describeEvent({ kind: 'refund', payload: { refund_amount: 450 } }), 'Отмечен возврат 450 ₸');
  assert.equal(describeEvent({ kind: 'blocked' }), 'Счёт остановлен: нужен действующий договор');
});

test('номер карты группируется по четыре', () => {
  assert.equal(cardNumberLabel('4400000000001234'), '4400 0000 0000 1234');
  assert.equal(cardNumberLabel(''), '');
});

test('колонки реестра: по умолчанию прежние семь, обязательные не снимаются, мусор выпадает', () => {
  assert.deepEqual(defaultRegistryColumns(), ['number', 'expense', 'counterparty', 'amount', 'stage', 'responsible', 'due']);
  assert.deepEqual(normalizeRegistryColumns(null), defaultRegistryColumns());
  assert.deepEqual(normalizeRegistryColumns(['notes', 'project', 'gone']), ['number', 'expense', 'project', 'notes'],
    'порядок — как в таблице, неизвестная колонка выпала, № и Расход вернулись');
  // п. 2 дополнения Дмитриевой: все поля реестра доступны колонками
  for (const key of ['project', 'branch', 'category', 'counterparty', 'amount', 'source', 'type', 'period',
    'stage', 'responsible', 'paid', 'notes', 'card', 'invoice']) {
    assert.ok(REGISTRY_COLUMNS.some((column) => column.key === key), key);
  }
});

test('вторая строка расхода не повторяет то, что стоит своей колонкой', () => {
  const request = { category_name: 'Аренда', project_name: 'iCORE офис', department_name: 'СЗоВ' };
  assert.equal(expenseSubline(request, []), 'Аренда · iCORE офис · СЗоВ');
  assert.equal(expenseSubline(request, ['project']), 'Аренда · СЗоВ');
  assert.equal(expenseSubline(request, ['project', 'category']), 'СЗоВ');
});

test('ответственный без «Бухгалтерия / Бухгалтерия» и с основанием по Приказу', () => {
  assert.deepEqual(responsibleLines({ status: 'active', current_role: 'accounting', current_assignee_name: 'Бухгалтерия', current_step: 8 }),
    { name: 'Бухгалтерия', sub: '' });
  assert.deepEqual(responsibleLines({ status: 'active', current_role: 'manager', current_assignee_name: 'Хайрихан Шерзад', current_step: 2 }),
    { name: 'Хайрихан Шерзад', sub: 'Руководитель' });
  assert.deepEqual(responsibleLines({ status: 'active', current_role: 'founder', current_assignee_id: 4, current_assignee_name: 'Алиева Зарина', current_step: 9, approval_order_number: '15' }),
    { name: 'Алиева Зарина', sub: 'по Приказу №15' });
  assert.deepEqual(responsibleLines({ status: 'done' }), { name: '—', sub: '' });
});

test('итоговая сумма — сумма минус возврат', () => {
  assert.equal(netAmount({ amount: 1200000 }), 1200000);
  assert.equal(netAmount({ amount: 1200000, refund_amount: 200000.5 }), 999999.5);
  assert.equal(netAmount({ amount: '54500', refund_amount: null }), 54500);
});

test('основание согласующего — открытым текстом, и причина, если Приказ не применён', () => {
  const byOrder = { approver_name: 'Алиева Зарина', order_id: 1, order_number: '15', evaluations: [{ order_id: 1, issued_on: '01.08.2026', applies: true }] };
  assert.equal(approverBasisLine(byOrder), 'по Приказу №15 от 01.08.2026 вместо Учредителя');
  const refused = { standard_label: 'Учредитель', evaluations: [{ order_id: 1, reason: 'Приказ №15 не применён. Причина: сумма счёта 6 000 000 ₸ превышает лимит 5 000 000 ₸.' }] };
  assert.ok(approverBasisLine(refused).startsWith('Приказ №15 не применён'));
  assert.equal(approverBasisLine({ standard_label: 'Учредитель', evaluations: [] }), '');
  assert.equal(approverBasisLine(null), '');
});

test('маршрут: пройденная голова сворачивается, последний пройденный остаётся на виду', () => {
  const steps = (states) => states.map((state, index) => ({ step_no: index + 1, state }));
  const early = splitRoute(steps(['done', 'current', 'pending']));
  assert.equal(early.folded.length, 0, 'два пройденных шага не сворачиваем');
  const mid = splitRoute(steps(['done', 'skipped', 'done', 'done', 'current', 'pending']));
  assert.deepEqual(mid.folded.map((s) => s.step_no), [1, 2, 3]);
  assert.deepEqual(mid.visible.map((s) => s.step_no), [4, 5, 6]);
  const closed = splitRoute(steps(['done', 'done', 'done', 'done']));
  assert.deepEqual(closed.visible.map((s) => s.step_no), [4]);
  assert.equal(passedStepsLabel(7), '7 пройденных шагов');
  assert.equal(passedStepsLabel(2), '2 пройденных шага');
  assert.equal(passedStepsLabel(21), '21 пройденный шаг');
});

test('динамика цены — только для того же товара', () => {
  const rows = [
    { request_id: 3, first_item: 'Бумага А4', unit_price: 2650 },
    { request_id: 2, first_item: 'Ручка', unit_price: 150 },
    { request_id: 1, first_item: 'бумага а4', unit_price: 2500 },
  ];
  assert.deepEqual(priceTrend(rows), { 3: 6 });
  assert.equal(trendLabel(6), '+6 %');
  assert.equal(trendLabel(-4), '−4 %');
});
