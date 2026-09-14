import test from 'node:test';
import assert from 'node:assert/strict';

import {
  STATE_FILTERS,
  STATE_META,
  cardNumberLabel,
  daysUntil,
  describeEvent,
  dueLabel,
  fmtDate,
  fmtDateTime,
  fmtMoney,
  itemsTotal,
  parseAmount,
  pluralDays,
  requestState,
  routeSummary,
  rowTone,
  stepLabel,
  toneRow,
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
