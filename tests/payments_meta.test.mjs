import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_UNIT,
  HINTS,
  ORIGINALS_OPTIONS,
  PARTY_KIND_OPTIONS,
  PAYMENT_ORDER_OPTIONS,
  POWER_OF_ATTORNEY_OPTIONS,
  PREVIOUSLY_PAID_OPTIONS,
  RECEIVED_OPTIONS,
  REGISTRY_COLUMNS,
  SOURCE_OPTIONS,
  STATE_FILTERS,
  STATE_META,
  SUPPLIER_KIND_OPTIONS,
  TYPE_OPTIONS,
  UNITS,
  VAT_OPTIONS,
  invoiceDescriptionDraft,
  isBlankItem,
  itemProblem,
  itemQuantity,
  itemTotal,
  lastPaymentToCounterparty,
  previousPaymentNote,
  receiptComment,
  requisitesDraft,
  stepReturnNote,
  supplierLabel,
  toScaled,
  unitOptions,
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

/* ── Позиции: количество, единица, цена, сумма ─────────────────────────────── */

test('единица измерения — из списка; вписанная руками в старой заявке не теряется', () => {
  assert.ok(UNITS.includes(DEFAULT_UNIT));
  assert.equal(new Set(UNITS).size, UNITS.length, 'единицы не повторяются');
  // в списке только слова: числу, которое примут за второе количество, там не место
  for (const unit of UNITS) assert.ok(!/^\d/.test(unit), unit);
  assert.deepEqual(unitOptions('шт').map((o) => o.value), UNITS);
  const legacy = unitOptions('бут.');
  assert.equal(legacy.length, UNITS.length + 1);
  assert.deepEqual(legacy[legacy.length - 1], { value: 'бут.', label: 'бут.' });
  assert.deepEqual(unitOptions('').map((o) => o.value), UNITS);
});

// Те же пары стоят в tests/test_payments_workflow.py (LINE_TOTALS): форма и сервер
// обязаны сойтись до тиына. Меняете одну таблицу — меняйте обе.
const LINE_TOTALS = [
  ['5', '2000', 10000],
  ['2,5', '10,01', 25.03],
  ['1,5', '33,33', 50],
  ['0,333', '3', 1],
  ['3', '0,335', 1.02],
  ['1,0005', '100', 100.1],
  ['0,0004', '100', 0],
  ['1234567,891', '98765,43', 121932628618.81],
  ['20', '2 500', 50000],
  ['7', '0,1', 0.7],
  ['0.1', '0.2', 0.02],
  ['3', '1 234,565', 3703.71],
  ['2.675', '1', 2.68],
  ['1', '2.675', 2.68],
];

test('строка считается как на сервере: количество × цена, до тиына, половина вверх', () => {
  for (const [quantity, price, expected] of LINE_TOTALS) {
    assert.equal(itemTotal({ quantity, unit_price: price }), expected, `${quantity} × ${price}`);
  }
  // цена приходит из поля числом — результат тот же
  assert.equal(itemTotal({ quantity: '2,5', unit_price: 10.01 }), 25.03);
  assert.equal(itemTotal({ quantity: 1.5, unit_price: 33.33 }), 50);
});

test('итог заявки — сумма строк, каждая округлена сама', () => {
  const line = { name: 'Сахар', quantity: '1,5', unit: 'кг', unit_price: 33.33 };
  assert.equal(itemsTotal([line, line]), 100, 'не 99,99: строки 50 + 50');
  assert.equal(itemsTotal([{ quantity: '0,1', unit_price: '0,1' }, { quantity: '0,2', unit_price: '0,1' }]), 0.03);
  assert.equal(itemsTotal([]), 0);
  assert.equal(itemsTotal(null), 0);
});

test('пустое количество — ноль, а не «одна штука»', () => {
  assert.equal(itemQuantity({ quantity: '' }), 0);
  assert.equal(itemQuantity({ quantity: null }), 0);
  assert.equal(itemQuantity({ quantity: '2,5' }), 2.5);
  assert.equal(itemQuantity({ quantity: ' 1 000 ' }), 1000);
  assert.equal(itemQuantity({ quantity: '1,2,3' }), 0, 'нечитаемое число — не количество');
  assert.equal(itemTotal({ quantity: '', unit_price: 700 }), 0);
});

test('что не так со строкой позиции — словами', () => {
  assert.equal(isBlankItem({ name: '', quantity: '1', unit: 'шт', unit_price: 0 }), true, 'новая строка — пустая');
  assert.equal(isBlankItem({ name: 'Бумага', quantity: '1', unit_price: 0 }), false);
  assert.equal(isBlankItem({ name: '', quantity: '1', unit_price: 500 }), false);
  assert.equal(itemProblem({ name: '', quantity: '1', unit_price: 0 }), '');
  assert.equal(itemProblem({ name: 'Бумага', quantity: '20', unit_price: 2500 }), '');
  assert.match(itemProblem({ name: '', quantity: '1', unit_price: 500 }), /нет названия/);
  assert.match(itemProblem({ name: 'Бумага', quantity: '', unit_price: 2500 }), /количество/);
  assert.match(itemProblem({ name: 'Бумага', quantity: '0', unit_price: 2500 }), /количество/);
  assert.match(itemProblem({ name: 'Бумага', quantity: '0,0004', unit_price: 2500 }), /количество/);
  assert.match(itemProblem({ name: 'Бумага', quantity: '1', unit_price: -5 }), /отрицательной/);
});

test('деньги округляются до тиына по записи числа, а не по двоичной дроби', () => {
  assert.equal(toScaled('2,5', 3), 2500);
  assert.equal(toScaled('1 234,565', 2), 123457);
  assert.equal(toScaled('-0,0005', 3), -1);
  assert.equal(toScaled('.5', 2), 50);
  assert.equal(toScaled('5.', 2), 500);
  assert.equal(toScaled('abc', 2), 0);
  assert.equal(toScaled(NaN, 2), 0);
  assert.ok(Object.is(toScaled('-0', 2), 0), 'минус ноль наружу не выходит');
  assert.equal(fmtMoney(0.999), '1 ₸', 'раньше выходило «0,100»');
  assert.equal(fmtMoney(1.005), '1,01 ₸');
  assert.equal(fmtMoney(0.994), '0,99 ₸');
  assert.equal(fmtMoney(0.1 + 0.2), '0,30 ₸');
  assert.equal(fmtMoney(-0.004), '0 ₸');
  assert.equal(fmtMoney(999999999999.99), '999 999 999 999,99 ₸');
});

/* ── Подсказки под «i» и варианты выбора ───────────────────────────────────── */

test('у каждой подсказки есть фраза «зачем поле», у вариантов — имя и смысл', () => {
  const keys = Object.keys(HINTS);
  assert.ok(keys.length >= 25);
  for (const key of keys) {
    const hint = HINTS[key];
    assert.ok(typeof hint.intro === 'string' && hint.intro.length >= 10, `${key}: нет вводной фразы`);
    for (const option of hint.options || []) {
      assert.equal(option.length, 2, `${key}: вариант — пара «имя, смысл»`);
      assert.ok(option[0] && option[1], `${key}: пустой вариант`);
    }
  }
});

test('подсказка называет ровно те варианты, что стоят в селекторе', () => {
  const pairs = [
    ['source', SOURCE_OPTIONS], ['type', TYPE_OPTIONS], ['supplierKind', SUPPLIER_KIND_OPTIONS],
    ['supplierVat', VAT_OPTIONS], ['vatPayer', VAT_OPTIONS], ['partyKind', PARTY_KIND_OPTIONS],
    ['powerOfAttorney', POWER_OF_ATTORNEY_OPTIONS], ['paymentOrder', PAYMENT_ORDER_OPTIONS],
    ['previouslyPaid', PREVIOUSLY_PAID_OPTIONS], ['received', RECEIVED_OPTIONS], ['originals', ORIGINALS_OPTIONS],
  ];
  for (const [key, options] of pairs) {
    assert.deepEqual(
      HINTS[key].options.map(([name]) => name).sort(),
      options.map((option) => option.label).sort(),
      `${key}: подсказка и селектор разошлись`,
    );
  }
});

test('выбор из двух вариантов: «нет» идёт первым и отличается от «не выбрано»', () => {
  for (const options of [VAT_OPTIONS, POWER_OF_ATTORNEY_OPTIONS, PAYMENT_ORDER_OPTIONS, PREVIOUSLY_PAID_OPTIONS]) {
    assert.deepEqual(options.map((option) => option.value), [false, true]);
  }
  assert.equal(supplierLabel({}), '');
  assert.equal(supplierLabel({ supplier_kind: 'too', supplier_vat: false }), 'ТОО, без НДС');
  assert.equal(supplierLabel({ supplier_kind: 'ip', supplier_vat: true }), 'ИП, с НДС');
  assert.equal(supplierLabel({ supplier_kind: 'other', supplier_vat: null }), 'Другое', 'НДС не выбран — о нём молчим');
  assert.equal(supplierLabel({ supplier_vat: false }), 'без НДС');
});

/* ── Заготовки текстов на шагах ────────────────────────────────────────────── */

test('шаг вернули на доработку — причина берётся из последнего события шага', () => {
  const events = [
    { step_no: 7, kind: 'step_done' },
    { step_no: 7, kind: 'returned', comment: 'Нет печати на счёте' },
    { step_no: 7, kind: 'attachment_added' },
  ];
  assert.equal(stepReturnNote(events, 7).comment, 'Нет печати на счёте');
  assert.equal(stepReturnNote([...events, { step_no: 7, kind: 'step_done' }], 7), null, 'после отписки возврат уже не показываем');
  assert.equal(stepReturnNote(events, 6), null);
  assert.equal(stepReturnNote(null, 7), null);
});

test('шаг 5: реквизиты подставляются из справочника юр. лиц', () => {
  assert.equal(requisitesDraft(null), '');
  assert.equal(requisitesDraft({ name: 'ТОО «Наше»', bin: '123456789012' }), 'ТОО «Наше», БИН 123456789012');
  assert.equal(
    requisitesDraft({ name: 'ТОО «Наше»', bin: '123456789012', requisites: ' АО «Банк», ИИК KZ00 ' }),
    'ТОО «Наше», БИН 123456789012\nАО «Банк», ИИК KZ00',
  );
  assert.equal(requisitesDraft({ name: 'ИП Пример' }), 'ИП Пример');
});

test('шаг 7: описание счёта собирается из заявки', () => {
  const request = {
    expense_name: 'Аренда офиса', payment_period: 'октябрь 2026', counterparty_name: 'ТОО «Бизнес-центр»',
    amount: 1200000, department_name: 'СЗоВ',
  };
  assert.equal(
    invoiceDescriptionDraft(request, 'ТОО «Наше»'),
    'Аренда офиса, октябрь 2026. Оплата с ТОО «Наше» на ТОО «Бизнес-центр». Сумма 1 200 000 ₸. Отдел: СЗоВ.',
  );
  assert.equal(
    invoiceDescriptionDraft({ expense_name: 'Бумага', amount: 500, counterparty_name: 'ИП Пример' }, ''),
    'Бумага. Оплата с … на ИП Пример. Сумма 500 ₸.',
  );
  assert.equal(invoiceDescriptionDraft(null, 'ТОО'), '');
});

test('шаг 8: отписка бухгалтерии складывается из выбора, а не пишется руками', () => {
  assert.equal(previousPaymentNote({ paid: false }), 'Ранее этому поставщику не платили');
  assert.equal(
    previousPaymentNote({ paid: true, paidOn: '2026-10-02', paidAmount: 74000 }),
    'Последняя оплата: 02.10.2026, 74 000 ₸',
  );
  assert.equal(previousPaymentNote({ paid: true, paidOn: '', paidAmount: 74000 }), '', '«платили» без даты — не ответ');
  assert.equal(previousPaymentNote({ paid: true, paidOn: '2026-10-02', paidAmount: 0 }), '');
  assert.equal(previousPaymentNote({ paid: null }), '', 'не выбрано — пусто, шаг не отпишется');
  const history = [
    { request_id: 5, matched: ['category'], paid_on: '2026-10-05' },
    { request_id: 4, matched: ['counterparty'], paid_on: null },
    { request_id: 3, matched: ['counterparty', 'category'], paid_on: '2026-10-02', paid_amount: 74000 },
  ];
  assert.equal(lastPaymentToCounterparty(history).request_id, 3, 'нужна оплата именно этому поставщику');
  assert.equal(lastPaymentToCounterparty([]), null);
});

test('шаг 11: отписка о получении — из двух выборов', () => {
  assert.equal(receiptComment({ received: 'goods', originals: 'handed' }), 'Получен товар. Оригинал накладной передан в бухгалтерию.');
  assert.equal(receiptComment({ received: 'service', originals: 'later' }), 'Получена услуга. Оригинал АВР передам в бухгалтерию позже.');
  assert.equal(receiptComment({ received: 'goods', originals: null }), '');
  assert.equal(receiptComment({ received: null, originals: 'handed' }), '');
});

test('история называет форму поставщика и НДС словами', () => {
  assert.equal(
    describeEvent({ kind: 'edited', payload: { changes: { supplier_kind: ['too', 'ip'], supplier_vat: [null, true] } } }),
    'Изменено: Форма поставщика, НДС поставщика',
  );
});
