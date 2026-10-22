import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  ACCOUNTING_CATEGORY_OPTIONS,
  ALTERNATIVES_OPTIONS,
  ASSET_SKELETON,
  ASSET_STATUS_OPTIONS,
  AUTO_CREATE_OPTIONS,
  BOARD_CHUNK_SIZES,
  BOARD_COLUMN_GAP,
  BOARD_COLUMN_MIN,
  BOARD_COLUMN_PEEK,
  BOARD_THUMB_MIN,
  CARD_FOLDS,
  DEFAULT_BOARD_CHUNK,
  CARD_RECIPIENT_OPTIONS,
  DEFAULT_UNIT,
  EMPTY_FILTERS,
  FILE_KINDS,
  HINTS,
  KIND_SHORT_OPTIONS,
  MANAGER_STEP_OPTIONS,
  MEMBER_ROLES,
  METHOD_SHORT_OPTIONS,
  OBJECT_TYPE_OPTIONS,
  PARTY_KIND_OPTIONS,
  PAYMENT_METHOD_OPTIONS,
  REGISTRY_COLUMNS,
  REQUEST_KIND_OPTIONS,
  REQUEST_SKELETON,
  STATE_FILTERS,
  STATE_META,
  STATE_OPTIONS,
  UNITS,
  UNSTAFFED,
  VAT_OPTIONS,
  activeFilterCount,
  approvalSummary,
  boardHidden,
  boardLayout,
  boardPageStep,
  boardScrollFor,
  boardThumb,
  canDropTo,
  cardDigits,
  cardFaces,
  cardLuhnOk,
  cardNumberLabel,
  cardProblem,
  daysUntil,
  defaultRegistryColumns,
  describeEvent,
  deskTaskAbout,
  deskTaskReturned,
  dueChip,
  dueLabel,
  expenseSubline,
  exportFileName,
  fileKindOptions,
  filesSummary,
  filtersToParams,
  fmtDate,
  fmtDateTime,
  fmtMoney,
  historySummary,
  initialsOf,
  invoiceProblems,
  isBlankItem,
  isBlankOffer,
  isOverdue,
  itemProblem,
  itemQuantity,
  itemTotal,
  itemsTotal,
  legacyProblems,
  netAmount,
  normalizeBoardChunk,
  normalizeRegistryColumns,
  pageRange,
  paidLabel,
  paidWord,
  parseAmount,
  plural,
  pluralDays,
  priceTrend,
  purchaseProblems,
  quantityText,
  recordView,
  regularProblems,
  registryTableMinWidth,
  routeSummary,
  registryWhere,
  REQUEST_TYPES,
  requestState,
  requestType,
  requisitesText,
  responsibleLines,
  rowTone,
  stageLine,
  toScaled,
  toneRow,
  trendLabel,
  unitOptions,
} from '../src/components/payments/paymentsMeta.js';

/* Правила раздела «Оплата счетов» (ТЗ «Закуп и оплата», #381), которые иначе
 * жили бы в разметке: цвет строки только у состояний со смыслом, деньги без
 * «прыгающих» форматов, проверка формы до отправки, куда можно перетащить
 * карточку доски, какие фильтры уходят в запрос. */

/* Деньги на экране набраны с неразрывным пробелом (тысячи и перед «₸»): сумма не
   должна разрываться переносом строки. В ожиданиях он пишется обычным пробелом
   и подставляется здесь — иначе разницу в тесте не увидеть глазами. */
const nb = (text) => text.replaceAll(' ', '\u00a0');

/* ── Состояния и цвет ──────────────────────────────────────────────────────── */

test('строка красится только у состояний со смыслом', () => {
  assert.equal(rowTone({ status: 'active', state: 'active' }), null);
  assert.equal(rowTone({ status: 'active', state: 'overdue' }), 'overdue');
  assert.equal(rowTone({ status: 'active', state: 'clarification' }), 'blocked');
  assert.equal(rowTone({ status: 'done', state: 'done' }), 'done');
  assert.equal(rowTone({ status: 'rejected', state: 'rejected' }), 'rejected');
  assert.equal(rowTone({ status: 'cancelled', state: 'cancelled' }), 'rejected');
  assert.equal(toneRow(null), '');
  assert.ok(toneRow('overdue').includes('rose'));
});

test('состояние берётся с сервера, а без него — из статуса', () => {
  assert.equal(requestState({ status: 'active' }), 'active');
  assert.equal(requestState({ status: 'done' }), 'done');
  assert.equal(requestState({ status: 'active', state: 'clarification' }), 'clarification');
  assert.equal(requestState(null), 'active');
});

test('легенда реестра и фильтр «Статус» досок покрывают все состояния', () => {
  assert.deepEqual(STATE_FILTERS.map((item) => item.key),
    ['all', 'mine', 'watching', 'open', 'clarification', 'overdue', 'done', 'rejected', 'cancelled']);
  // «Наблюдаю» — срез по смотрящему, как «Ждут меня», а не состояние: без кружка, счётчик — свой.
  const watching = STATE_FILTERS.find((item) => item.key === 'watching');
  assert.deepEqual(watching, { key: 'watching', label: 'Наблюдаю', counter: 'watching', tone: null });
  for (const filter of STATE_FILTERS) {
    if (filter.tone) assert.ok(Object.values(STATE_META).some((meta) => meta.tone === filter.tone), filter.key);
  }
  // Отклонённые и отменённые считаются раздельно: общий счётчик расходился с отбором.
  assert.notEqual(STATE_FILTERS.find((item) => item.key === 'rejected').counter,
    STATE_FILTERS.find((item) => item.key === 'cancelled').counter);
  assert.deepEqual(STATE_OPTIONS.map((item) => item.value),
    ['open', 'clarification', 'overdue', 'done', 'rejected', 'cancelled']);
});

/* ── Деньги и даты ─────────────────────────────────────────────────────────── */

test('деньги: разбор строки с пробелами и запятой, формат с узким пробелом и ₸', () => {
  assert.equal(parseAmount('1 234,50'), 1234.5);
  assert.equal(parseAmount('300 000 ₸'), 300000);
  assert.equal(parseAmount(''), 0);
  assert.equal(parseAmount('abc'), 0);
  assert.equal(fmtMoney(1234567.5), nb('1 234 567,50 ₸'));
  assert.equal(fmtMoney(300000), nb('300 000 ₸'));
  assert.equal(fmtMoney(2500, { currency: false }), nb('2 500'));
  assert.equal(fmtMoney(-15), nb('−15 ₸'));
});

test('даты: dd.mm.yyyy без часового сдвига, срок словами', () => {
  assert.equal(fmtDate('2026-09-14'), '14.09.2026');
  assert.equal(fmtDateTime('2026-09-14T10:58:00'), '14.09.2026 10:58');
  assert.equal(fmtDate(null), '—');
  assert.equal(daysUntil('2026-09-20', '2026-09-14'), 6);
  assert.equal(pluralDays(1), '1 день');
  assert.equal(pluralDays(3), '3 дня');
  assert.equal(pluralDays(11), '11 дней');
  assert.equal(plural(3, ['варианта поставщика', 'вариантов поставщиков', 'вариантов поставщиков']), '3 вариантов поставщиков');
  assert.equal(plural(1, ['заявка', 'заявки', 'заявок']), '1 заявка');
  assert.equal(dueLabel({ due_on: '2026-09-20', status: 'active' }, '2026-09-14'), 'через 6 дней');
  assert.equal(dueLabel({ due_on: '2026-09-10', status: 'active' }, '2026-09-14'), 'просрочен на 4 дня');
  assert.equal(dueLabel({ due_on: '2026-09-14', status: 'active' }, '2026-09-14'), 'сегодня');
});

test('срок важен до оплаты: оплаченная и закрытая заявка не «просрочена»', () => {
  const late = { due_on: '2026-09-10', status: 'active' };
  assert.equal(isOverdue(late, '2026-09-14'), true);
  assert.equal(isOverdue({ ...late, paid_on: '2026-09-12' }, '2026-09-14'), false);
  assert.equal(isOverdue({ ...late, status: 'done' }, '2026-09-14'), false);
  assert.equal(isOverdue({ status: 'active' }, '2026-09-14'), false);
  assert.equal(dueLabel({ ...late, paid_on: '2026-09-12' }, '2026-09-14'), 'к 10.09.2026');
  assert.equal(dueLabel({ ...late, status: 'rejected' }, '2026-09-14'), 'к 10.09.2026');
});

test('итоговая сумма — сумма минус возврат', () => {
  assert.equal(netAmount({ amount: 1200000 }), 1200000);
  assert.equal(netAmount({ amount: 1200000, refund_amount: 200000.5 }), 999999.5);
  assert.equal(netAmount({ amount: '54500', refund_amount: null }), 54500);
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
  assert.equal(itemsTotal([{ name: 'Бумага А4', quantity: 1, unit_price: 2500 }, { name: 'Карандаш', quantity: 3, unit_price: 150 },
    { name: '', quantity: '', unit_price: '' }]), 2950);
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
  assert.equal(fmtMoney(0.999), nb('1 ₸'), 'раньше выходило «0,100»');
  assert.equal(fmtMoney(1.005), nb('1,01 ₸'));
  assert.equal(fmtMoney(0.994), nb('0,99 ₸'));
  assert.equal(fmtMoney(0.1 + 0.2), nb('0,30 ₸'));
  assert.equal(fmtMoney(-0.004), nb('0 ₸'));
  assert.equal(fmtMoney(999999999999.99), nb('999 999 999 999,99 ₸'));
});

test('«количество получено» подставляется из позиций заявки — как на сервере', () => {
  assert.equal(quantityText([{ quantity: '10', unit: 'уп' }, { quantity: 2.5, unit: 'кг' }]), '10 уп; 2,5 кг');
  assert.equal(quantityText([{ quantity: 1, unit: '' }]), '1');
  assert.equal(quantityText([]), '');
});

/* ── Форма заявки: чего не хватает (ТЗ, п. 4) ──────────────────────────────── */

const filledPurchase = (patch = {}) => ({
  legal_entity_id: 1, department_id: 5, expense_name: 'Бумага А4', category_id: 3,
  justification: 'Закончилась бумага', due_on: '2026-10-20', object_type: 'service', accounting_category: null,
  payment_method: 'invoice',
  items: [{ name: 'Бумага А4', quantity: '10', unit: 'уп', unit_price: 10600 }],
  offers: [
    { counterparty_id: 11, supplier_name: '', amount: 106000, is_recommended: true },
    { counterparty_id: 12, supplier_name: '', amount: 110000, is_recommended: false },
    { counterparty_id: null, supplier_name: 'ИП Новый', amount: 120000, is_recommended: false },
  ],
  no_alternatives: false, supplier_choice_reason: 'Дешевле остальных',
  ...patch,
});

test('новый закуп: заполненная форма уходит, каждое обязательное поле названо словами', () => {
  assert.deepEqual(purchaseProblems(filledPurchase()), []);
  const cases = [
    [{ department_id: null }, 'укажите подразделение'],
    [{ expense_name: ' ' }, 'назовите закуп'], [{ category_id: null }, 'выберите категорию закупа'],
    [{ justification: '' }, 'опишите и обоснуйте закуп'], [{ due_on: '' }, 'укажите желаемый срок'],
    [{ object_type: null }, 'выберите: товар или услуга'], [{ payment_method: null }, 'выберите способ оплаты'],
    [{ items: [{ name: '', quantity: '1', unit_price: 0 }] }, 'добавьте хотя бы одну позицию'],
    [{ supplier_choice_reason: '' }, 'обоснуйте выбор поставщика'],
  ];
  for (const [patch, text] of cases) {
    assert.ok(purchaseProblems(filledPurchase(patch)).includes(text), text);
  }
  // Компания необязательна (просьба владельца 15.10.2026): не знает её инициатор — укажет бухгалтерия.
  assert.deepEqual(purchaseProblems(filledPurchase({ legal_entity_id: null })), []);
  // У товара обязательна категория учёта (п. 10); у услуги её нет.
  assert.ok(purchaseProblems(filledPurchase({ object_type: 'goods' })).includes('выберите категорию учёта'));
  assert.deepEqual(purchaseProblems(filledPurchase({ object_type: 'goods', accounting_category: 'asset' })), []);
});

test('поставщики: минимум настраивается, рекомендуемый один, пустые строки не в счёт', () => {
  const two = filledPurchase();
  two.offers = two.offers.slice(0, 2);
  assert.ok(purchaseProblems(two).includes('нужно не меньше 3 вариантов поставщиков'));
  assert.deepEqual(purchaseProblems(two, { minSuppliers: 2 }), []);
  const blank = { counterparty_id: null, supplier_name: '', amount: 0, terms: '', link: '', comment: '' };
  assert.equal(isBlankOffer(blank), true);
  assert.ok(purchaseProblems({ ...two, offers: [...two.offers, blank] }).some((text) => text.startsWith('нужно не меньше')),
    'пустая строка формы поставщиком не считается');
  const noStar = filledPurchase();
  noStar.offers = noStar.offers.map((offer) => ({ ...offer, is_recommended: false }));
  assert.ok(purchaseProblems(noStar).includes('отметьте рекомендуемого поставщика'));
  const noPrice = filledPurchase();
  noPrice.offers[1] = { ...noPrice.offers[1], amount: 0 };
  assert.ok(purchaseProblems(noPrice).includes('у каждого поставщика укажите название и стоимость'));
});

test('«альтернатив нет»: причина обязательна, а единственного поставщика отмечать не нужно', () => {
  const single = filledPurchase({
    no_alternatives: true, no_alternatives_reason: null, supplier_choice_reason: '',
    offers: [{ counterparty_id: 11, supplier_name: '', amount: 106000, is_recommended: false }],
  });
  assert.deepEqual(purchaseProblems(single), ['выберите причину отсутствия альтернатив']);
  assert.deepEqual(purchaseProblems({ ...single, no_alternatives_reason: 'other' }), ['опишите причину отсутствия альтернатив']);
  assert.deepEqual(purchaseProblems({ ...single, no_alternatives_reason: 'other', no_alternatives_comment: 'Только он' }), []);
  // Так форма и отправляет: флаг «рекомендуемый» единственному поставщику ставит она сама.
  assert.deepEqual(purchaseProblems({ ...single, no_alternatives_reason: 'single_supplier' }), []);
  assert.ok(purchaseProblems({ ...single, no_alternatives_reason: 'single_supplier', offers: [] }).includes('укажите поставщика и стоимость'));
});

test('пополнение карты: получатель, владелец, номер и назначение', () => {
  const card = filledPurchase({ payment_method: 'card', card_recipient: null, card_holder_name: '', card_number: '', payment_purpose: '' });
  const problems = purchaseProblems(card);
  for (const text of ['выберите, чью карту пополнить', 'укажите полный номер карты', 'укажите назначение перевода']) {
    assert.ok(problems.includes(text), text);
  }
  const employee = { ...card, card_recipient: 'employee', card_holder_user_id: 7, card_number: '4400430212345678', payment_purpose: 'На картриджи' };
  assert.deepEqual(purchaseProblems(employee), []);
  assert.ok(purchaseProblems({ ...employee, card_holder_user_id: null }).includes('укажите сотрудника'));
  // У карты поставщика владельца вписывают: сотрудник, подставленный формой по
  // умолчанию, его не заменяет — иначе форма пускала к отправке, а сервер отказывал.
  const supplier = { ...employee, card_recipient: 'supplier', card_holder_user_id: 7, card_holder_name: '' };
  assert.deepEqual(purchaseProblems(supplier), ['укажите ФИО владельца карты']);
  assert.deepEqual(purchaseProblems({ ...supplier, card_holder_name: 'Иванов И.' }), []);
  // Карта из справочника либо уже сохранённая в заявке — номер заново не нужен.
  assert.deepEqual(purchaseProblems({ ...employee, card_number: '', card_id: 3 }), []);
  assert.deepEqual(purchaseProblems({ ...employee, card_number: '' }, { hasCard: true }), []);
});

test('способ оплаты и категория учёта, которые некому выполнить, видны до отправки', () => {
  const card = filledPurchase({ payment_method: 'card', card_recipient: 'employee', card_holder_user_id: 7,
    card_number: '4400430212345678', payment_purpose: 'На картриджи' });
  assert.deepEqual(purchaseProblems(card, { staffed: { finance: true } }), []);
  assert.deepEqual(purchaseProblems(card, { staffed: { finance: false } }), [UNSTAFFED.finance]);
  assert.deepEqual(purchaseProblems(card), [], 'роли ещё не загружены — форма не пугает раньше времени');
  const asset = filledPurchase({ object_type: 'goods', accounting_category: 'asset' });
  assert.deepEqual(purchaseProblems(asset, { staffed: { asset_keeper: false } }), [UNSTAFFED.asset_keeper]);
  assert.deepEqual(purchaseProblems({ ...asset, accounting_category: 'consumable' }, { staffed: { asset_keeper: false } }), []);
});

test('регулярный платёж с пробелами в справочнике не доходит до отказа сервера', () => {
  const ready = { fixed_template_id: 9, department_id: 5, payment_period: 'октябрь 2026', amount: 480000,
    invoice_number: 'ОБЛ-1', invoice_date: '2026-10-08' };
  const full = { counterparty_id: 11, legal_entity_id: 1, object_type: 'service' };
  assert.deepEqual(regularProblems(ready, { fileKinds: ['invoice'], template: full }), []);
  const [gap] = regularProblems(ready, { fileKinds: ['invoice'], template: { ...full, legal_entity_id: null, counterparty_id: null } });
  assert.match(gap, /не указаны: поставщик, компания/);
  const [goods] = regularProblems(ready, { fileKinds: ['invoice'], template: { ...full, object_type: 'goods' } });
  assert.match(goods, /категория учёта товара/);
  assert.deepEqual(regularProblems(ready, { fileKinds: ['invoice'], template: { ...full, object_type: 'goods', accounting_category: 'asset' },
    staffed: { asset_keeper: false } }), [UNSTAFFED.asset_keeper]);
});

test('заявка первой версии процесса: только компания и счёт', () => {
  const legacy = filledPurchase({ offers: [], justification: '', due_on: '', department_id: null });
  assert.ok(purchaseProblems(legacy).length > 3, 'по новым правилам ей не хватало бы многого');
  assert.deepEqual(legacyProblems(legacy), ['приложите счёт', 'укажите номер счёта', 'укажите дату счёта']);
  assert.deepEqual(legacyProblems({ ...legacy, invoice_number: 'СЧ-7', invoice_date: '2026-10-01' }, { fileKinds: ['invoice'] }), []);
  assert.deepEqual(legacyProblems({ ...legacy, legal_entity_id: null, invoice_number: 'СЧ-7', invoice_date: '2026-10-01' },
    { fileKinds: ['invoice'] }), ['выберите компанию']);
});

test('счёт у нового закупа можно приложить позже, но начатый — заполнить целиком', () => {
  assert.deepEqual(purchaseProblems(filledPurchase()), []);
  const started = purchaseProblems(filledPurchase({ invoice_number: '118' }));
  assert.deepEqual(started, ['приложите счёт', 'укажите дату счёта']);
  assert.deepEqual(purchaseProblems(filledPurchase({ invoice_number: '118', invoice_date: '2026-10-01' }), { fileKinds: ['invoice'] }), []);
  assert.deepEqual(invoiceProblems({}, []), ['приложите счёт', 'укажите номер счёта', 'укажите дату счёта']);
});

test('регулярный платёж: период, сумма и счёт (п. 4.4)', () => {
  assert.deepEqual(regularProblems({}), ['выберите регулярный платёж', 'укажите подразделение', 'укажите период оплаты',
    'укажите сумму', 'приложите счёт', 'укажите номер счёта', 'укажите дату счёта']);
  const ready = { fixed_template_id: 9, department_id: 5, payment_period: 'октябрь 2026', amount: 480000,
    invoice_number: 'ОБЛ-1', invoice_date: '2026-10-08' };
  assert.deepEqual(regularProblems(ready, { fileKinds: ['invoice'] }), []);
});

/* ── Карта (п. 5.2) ────────────────────────────────────────────────────────── */

test('номер карты: группы по четыре, длина, контрольная цифра', () => {
  assert.equal(cardNumberLabel('4400000000001234'), '4400 0000 0000 1234');
  assert.equal(cardNumberLabel(''), '');
  assert.equal(cardDigits('4400 0000-0000 1234'), '4400000000001234');
  assert.equal(cardProblem(''), 'Укажите номер карты');
  assert.equal(cardProblem('1234'), 'Номер карты: от 13 до 19 цифр');
  assert.equal(cardProblem('4400 0000 0000 1234'), '');
  assert.equal(cardLuhnOk('4111 1111 1111 1111'), true);
  assert.equal(cardLuhnOk('4111 1111 1111 1112'), false);
  assert.equal(cardLuhnOk(''), false);
});

/* ── Доски (пп. 7–9) ───────────────────────────────────────────────────────── */

test('карточку двигают между рабочими колонками; в итоговые ведёт действие', () => {
  const board = { code: 'accounting', work: ['new', 'checking', 'ready', 'paying'] };
  const card = { column: 'new', can_act: true, subtask: { kind: 'invoice_payment', state: 'open' } };
  assert.equal(canDropTo(board, card, 'checking'), true);
  assert.equal(canDropTo(board, card, 'paying'), true);
  assert.equal(canDropTo(board, card, 'new'), false, 'в свою же колонку — не перенос');
  assert.equal(canDropTo(board, card, 'paid'), false, '«Оплачено» ставит действие с платёжкой');
  assert.equal(canDropTo(board, card, 'clarification'), false);
  assert.equal(canDropTo(board, { ...card, can_act: false }, 'checking'), false, 'чужую задачу не двигают');
  assert.equal(canDropTo(board, { ...card, subtask: { state: 'waiting' } }, 'checking'), false);
  assert.equal(canDropTo(board, { ...card, column: 'paid', subtask: { state: 'done' } }, 'checking'), false);
  assert.equal(canDropTo(null, card, 'checking'), false);
  // «Уточнение компании»: компанию выбирают в окне заявки — ни из колонки, ни в колонку не перетаскивают.
  const clarifying = { column: 'entity', can_act: true, subtask: { kind: 'entity_clarify', state: 'open' } };
  assert.equal(canDropTo(board, clarifying, 'new'), false);
  assert.equal(canDropTo(board, card, 'entity'), false);
});

/* ── Фильтры (п. 19) ───────────────────────────────────────────────────────── */

test('в запрос уходят только выбранные фильтры', () => {
  assert.equal(filtersToParams(EMPTY_FILTERS).toString(), '');
  assert.equal(activeFilterCount(EMPTY_FILTERS), 0);
  const filters = { ...EMPTY_FILTERS, legal_entity_id: 2, request_kind: 'regular', state: 'overdue', overdue: true,
    amount_from: 1000, amount_to: 5000, date_from: '2026-10-01', date_to: '2026-10-08' };
  const params = filtersToParams(filters);
  assert.equal(params.get('legal_entity_id'), '2');
  assert.equal(params.get('request_kind'), 'regular');
  assert.equal(params.get('state'), 'overdue');
  assert.equal(params.get('overdue'), '1');
  assert.equal(params.get('mine'), null);
  assert.equal(params.get('amount_to'), '5000');
  // «Сумма от/до» и «Создана с/по» — по одному фильтру, а не по два.
  assert.equal(activeFilterCount(filters), 6);
});

test('набор фильтров — тринадцать из п. 19', () => {
  // компания, отдел, инициатор, исполнитель, согласующий, поставщик, тип заявки,
  // способ оплаты, статус, сумма (от/до), дата (с/по), просрочено, только мои
  assert.deepEqual(Object.keys(EMPTY_FILTERS).sort(), ['amount_from', 'amount_to', 'approver_id', 'assignee_id',
    'counterparty_id', 'date_from', 'date_to', 'department_id', 'initiator_id', 'legal_entity_id', 'mine', 'overdue',
    'payment_method', 'request_kind', 'state'].sort());
});

/* ── Реестр ────────────────────────────────────────────────────────────────── */

test('колонки реестра: набор по умолчанию помещается на экран, обязательные не снимаются', () => {
  assert.deepEqual(defaultRegistryColumns(),
    ['number', 'expense', 'counterparty', 'amount', 'method', 'stage', 'responsible', 'due']);
  assert.deepEqual(normalizeRegistryColumns(null), defaultRegistryColumns());
  assert.deepEqual(normalizeRegistryColumns(['notes', 'project', 'gone']), ['number', 'expense', 'project', 'notes'],
    'порядок — как в таблице, неизвестная колонка выпала, № и «Закуп» вернулись');
  for (const key of ['kind', 'entity', 'project', 'category', 'counterparty', 'amount', 'method', 'period',
    'stage', 'responsible', 'due', 'paid', 'invoice', 'docs', 'notes']) {
    assert.ok(REGISTRY_COLUMNS.some((column) => column.key === key), key);
  }
  // Раздел шириной 1400 px минус поля: набор по умолчанию не должен требовать прокрутки вбок.
  assert.ok(registryTableMinWidth(defaultRegistryColumns()) <= 1316, registryTableMinWidth(defaultRegistryColumns()));
});

test('вторая строка закупа не повторяет то, что стоит своей колонкой', () => {
  const request = { category_name: 'Аренда', project_name: 'iCORE офис', department_name: 'СЗоВ' };
  assert.equal(expenseSubline(request, []), 'Аренда · iCORE офис · СЗоВ');
  assert.equal(expenseSubline(request, ['project']), 'Аренда · СЗоВ');
  assert.equal(expenseSubline(request, ['project', 'category']), 'СЗоВ');
});

test('исполнитель: у задачи подразделения — подразделение, без «Бухгалтерия / Бухгалтерия»', () => {
  assert.deepEqual(responsibleLines({ status: 'active', current_role: 'accounting', current_assignee_name: null }),
    { name: 'Бухгалтерия', sub: '' });
  assert.deepEqual(responsibleLines({ status: 'active', current_role: 'finance', current_assignee_name: 'Финансовый отдел' }),
    { name: 'Финансовый отдел', sub: '' });
  assert.deepEqual(responsibleLines({ status: 'active', current_role: 'manager', current_assignee_name: 'Жумабаев Тимур' }),
    { name: 'Жумабаев Тимур', sub: 'Непосредственный руководитель' });
  assert.deepEqual(responsibleLines({ status: 'done' }), { name: '—', sub: '' });
});

test('где заявка сейчас — этап, а у возвращённой — «требуется уточнение»', () => {
  assert.equal(stageLine({ status: 'active', state: 'active', stage_label: 'Оплата' }), 'Оплата');
  assert.equal(stageLine({ status: 'active', state: 'clarification', clarify_from: 'invoice_payment' }), 'Требуется уточнение');
  assert.equal(stageLine({ status: 'active', state: 'clarification', clarify_from: null }), 'У инициатора');
  assert.equal(stageLine({ status: 'done' }), 'Закрыта');
  assert.equal(stageLine({ status: 'rejected' }), 'Отклонена');
  assert.equal(stageLine({ status: 'cancelled' }), 'Отменена');
});

test('метка срока — как у «Задач»: флажок и дата, смысл в тоне, подробности в подсказке', () => {
  const today = '2026-10-09';
  const at = (due_on, extra = {}) => dueChip({ status: 'active', due_on, ...extra }, { today });
  assert.deepEqual(at('2026-10-19'), { tone: 'normal', icon: 'flag', label: '19.10', title: 'Срок оплаты 19.10.2026 — через 10 дней' });
  // Жёлтой метка становится за три дня до срока — порог по умолчанию.
  assert.equal(at('2026-10-12').tone, 'soon');
  assert.equal(at('2026-10-13').tone, 'normal');
  assert.deepEqual(at('2026-10-09'), { tone: 'soon', icon: 'flag', label: '09.10', title: 'Срок оплаты — сегодня, 09.10.2026' });
  assert.deepEqual(at('2026-10-05'), { tone: 'overdue', icon: 'flag', label: '05.10', title: 'Срок оплаты 05.10.2026 — просрочен на 4 дня' });
  assert.equal(at('2026-09-28').title, 'Срок оплаты 28.09.2026 — просрочен на 11 дней');
  // Порог — настройка раздела (та же, по которой уходит напоминание о сроке).
  assert.equal(dueChip({ status: 'active', due_on: '2026-10-16' }, { today, soonDays: 7 }).tone, 'soon');
  // Оплаченной срок уже не грозит: вместо него — галочка и день оплаты, счётом или картой.
  assert.deepEqual(at('2026-10-05', { paid_on: '2026-10-08', payment_method: 'invoice' }),
    { tone: 'done', icon: 'check', label: '08.10', title: 'Оплачено 08.10.2026' });
  assert.equal(at('2026-10-05', { paid_on: '2026-10-08', payment_method: 'card' }).title, 'Пополнено 08.10.2026');
  assert.equal(dueChip({ status: 'done', due_on: '2026-10-05', paid_on: '2026-10-08' }, { today }).label, '08.10');
  // Срока нет либо заявка закрыта без оплаты — показывать нечего.
  assert.equal(at(null), null);
  assert.equal(dueChip({ status: 'rejected', due_on: '2026-10-05' }, { today }), null);
  assert.equal(dueChip({ status: 'cancelled', due_on: '2026-10-05' }, { today }), null);
  assert.equal(dueChip(null), null);
});

test('день оплаты в карточке заявки: счёт — «оплачено», карта — «пополнено»', () => {
  assert.equal(paidLabel({ paid_on: '2026-10-08', payment_method: 'invoice' }), 'оплачено 08.10');
  assert.equal(paidLabel({ paid_on: '2026-10-08T10:00:00', payment_method: 'card' }), 'пополнено 08.10');
  assert.equal(paidLabel({ due_on: '2026-10-08' }), '');
  assert.equal(paidLabel(null), '');
});

/* Жалоба 13.10.2026: карту сотрудника пополнили — и раздел сразу написал, что оплата
   проведена, хотя чека ещё нет (п. 5.3). Перевод на карту раздел называет пополнением
   везде, где показывает день, когда ушли деньги: в метке срока, в карточке, в реестре.
   Карточку проверяет payments_card_paid_word_render.test.mjs — по разметке. */
test('перевод на карту раздел называет пополнением, а не оплатой', () => {
  assert.equal(paidWord({ payment_method: 'card' }), 'Пополнено');
  assert.equal(paidWord({ payment_method: 'invoice' }), 'Оплачено');
  assert.equal(paidWord({}), 'Оплачено');
  assert.equal(paidWord(null), 'Оплачено');
  // Ячейка срока в реестре: слово берётся из paidLabel, в разметку оно не вписано.
  const registry = readFileSync(new URL('../src/components/payments/PaymentsView.jsx', import.meta.url), 'utf8');
  assert.match(registry, /\{paidLabel\(request\)\}/);
  assert.doesNotMatch(registry, /оплачено \{/);
});

test('тип заявки на карточке: закуп по счёту, закуп на карту, регулярный платёж — у каждого свой цвет', () => {
  assert.equal(requestType({ request_kind: 'purchase', payment_method: 'invoice' }).label, 'Закуп по счёту');
  assert.equal(requestType({ request_kind: 'purchase', payment_method: 'card' }).label, 'Закуп на карту');
  // Регулярный платёж идёт по счёту, но тип у него свой.
  assert.equal(requestType({ request_kind: 'regular', payment_method: 'invoice' }).key, 'regular');
  // Черновик закупа без способа оплаты — просто «Новый закуп», цвет закупа.
  assert.deepEqual([requestType({ request_kind: 'purchase' }).label, requestType({ request_kind: 'purchase' }).key], ['Новый закуп', 'purchase']);
  assert.equal(requestType(null).key, 'purchase');
  // Цвета разные у всех трёх, и ни один не из «смысловых» раздела (срок, уточнение, сделано).
  const bars = Object.values(REQUEST_TYPES).map((type) => type.bar);
  assert.equal(new Set(bars).size, 3);
  for (const type of Object.values(REQUEST_TYPES)) {
    assert.doesNotMatch(`${type.bar} ${type.pill}`, /rose|red|amber|yellow|emerald|green/, type.label);
    assert.match(type.bar, /^before:bg-[a-z]+-\d{3}$/);
  }
});

test('порция колонки — 20, 40 или 60, как у доски «Задач»; чужое значение — порция по умолчанию', () => {
  assert.deepEqual(BOARD_CHUNK_SIZES, [20, 40, 60]);
  assert.equal(DEFAULT_BOARD_CHUNK, 20);
  assert.equal(normalizeBoardChunk('40'), 40);
  assert.equal(normalizeBoardChunk(60), 60);
  for (const junk of [null, undefined, '', '25', 0, -20, 'abc', 1000]) assert.equal(normalizeBoardChunk(junk), 20, String(junk));
});

test('страница списка: границы, последняя неполная, номер за концом прижимается', () => {
  assert.deepEqual(pageRange(1, 20, 75), { page: 1, totalPages: 4, offset: 0, from: 1, to: 20 });
  assert.deepEqual(pageRange(4, 20, 75), { page: 4, totalPages: 4, offset: 60, from: 61, to: 75 });
  // Задачи закрыли, и пятой страницы уже нет — встаём на последнюю.
  assert.deepEqual(pageRange(5, 20, 75), { page: 4, totalPages: 4, offset: 60, from: 61, to: 75 });
  assert.deepEqual(pageRange(0, 20, 75).page, 1);
  assert.deepEqual(pageRange('2', 50, 292), { page: 2, totalPages: 6, offset: 50, from: 51, to: 100 });
  // Пустой список — одна пустая страница.
  assert.deepEqual(pageRange(3, 20, 0), { page: 1, totalPages: 1, offset: 0, from: 0, to: 0 });
});

test('инициалы в кружке — первые буквы двух первых слов, как у «Задач»', () => {
  assert.equal(initialsOf('Ерсултан Айгерим'), 'ЕА');
  assert.equal(initialsOf('Хабыл Асыл Айбарқызы'), 'ХА');
  assert.equal(initialsOf('  зайцева   мария '), 'ЗМ');
  assert.equal(initialsOf('Бухгалтерия'), 'Б');
  assert.equal(initialsOf(''), '?');
  assert.equal(initialsOf(null), '?');
});

test('лица карточки: инициатор → у кого этап; подразделение — значком; в итоговых колонках — кто закрыл', () => {
  const base = { initiator_name: 'Ерсултан Айгерим', column: 'new' };
  // Согласование у конкретного человека.
  assert.deepEqual(cardFaces({ ...base, subtask: { kind: 'approval', state: 'open', assignee_name: 'Жумабаев Тимур', role_label: 'Утвердитель' } }), {
    from: { name: 'Ерсултан Айгерим', title: 'Инициатор: Ерсултан Айгерим', role: false },
    to: { name: 'Жумабаев Тимур', title: 'Согласует: Жумабаев Тимур', role: false },
  });
  // Счёт у бухгалтерии целиком — это подразделение, а не человек.
  assert.deepEqual(cardFaces({ ...base, subtask: { kind: 'invoice_payment', state: 'open', assignee_name: 'Бухгалтерия', role_label: 'Бухгалтерия' } }).to,
    { name: 'Бухгалтерия', title: 'Исполнитель: Бухгалтерия', role: true });
  // Выполнено: кто согласовал, кто отклонил, кто оплатил.
  assert.equal(cardFaces({ ...base, column: 'approved', subtask: { kind: 'approval', state: 'done', done_by_name: 'Молдагалиева Сауле', role_label: 'Утвердитель' } }).to.title,
    'Согласовал: Молдагалиева Сауле');
  assert.equal(cardFaces({ ...base, column: 'rejected', subtask: { kind: 'approval', state: 'done', outcome: 'rejected', done_by_name: 'Жумабаев Тимур' } }).to.title,
    'Отклонил: Жумабаев Тимур');
  assert.equal(cardFaces({ ...base, column: 'paid', subtask: { kind: 'invoice_payment', state: 'done', done_by_name: 'Хабыл Асыл' } }).to.title,
    'Оплатил: Хабыл Асыл');
  // Вернули инициатору с вопросом — одно лицо: ждут его самого.
  assert.deepEqual(cardFaces({ ...base, column: 'clarification', subtask: { kind: 'invoice_payment', state: 'waiting', assignee_name: 'Бухгалтерия' } }),
    { from: { name: 'Ерсултан Айгерим', title: 'Ждём ответа: Ерсултан Айгерим', role: false }, to: null });
  // Инициатор сам себе исполнитель (своя заявка у него же) — второе лицо не повторяем.
  assert.equal(cardFaces({ ...base, subtask: { kind: 'receiving', state: 'open', assignee_name: 'Ерсултан Айгерим' } }).to, null);
  // Без подзадачи и без инициатора — лиц нет.
  assert.deepEqual(cardFaces({}), { from: null, to: null });
});

test('задача стола «вернули мне» — доработка и запрос; очередной шаг цветом не отмечен', () => {
  assert.equal(deskTaskReturned({ task: 'initiation', subtask: { status: 'rework' } }), true);
  assert.equal(deskTaskReturned({ task: 'initiation', subtask: { status: 'clarification' } }), true);
  assert.equal(deskTaskReturned({ task: 'initiation', clarify_comment: 'Нет номера счёта' }), true);
  assert.equal(deskTaskReturned({ task: 'initiation', subtask: { status: 'new' } }), false);
  // «rework» у чужого этапа — не возврат инициатору.
  assert.equal(deskTaskReturned({ task: 'approval', subtask: { status: 'rework' } }), false);
  assert.equal(deskTaskReturned({ task: 'docs_needed', subtask: null }), false);
  assert.equal(deskTaskReturned(null), false);
});

test('строка задачи стола не повторяет человеку его же имя', () => {
  const task = { id: 211, payment_method: 'invoice', initiator_name: 'Ерсултан Айгерим', department_name: 'HR' };
  assert.equal(deskTaskAbout(task), '№211 · Счёт · Ерсултан Айгерим · HR');
  assert.equal(deskTaskAbout({ ...task, own: true }), '№211 · Счёт · HR');
  assert.equal(deskTaskAbout({ id: 5, payment_method: 'card' }), '№5 · Карта');
  assert.equal(deskTaskAbout({ id: 5 }), '№5');
});

test('карточка реестра на телефоне: этап и у кого он, одно слово дважды не печатается', () => {
  const active = { status: 'active', state: 'active' };
  assert.equal(registryWhere({ ...active, stage_label: 'Согласование', current_role: 'approver', current_assignee_name: 'Жумабаев Тимур' }),
    'Согласование · Жумабаев Тимур');
  assert.equal(registryWhere({ ...active, stage_label: 'Оплата', current_role: 'accounting' }), 'Оплата · Бухгалтерия');
  assert.equal(registryWhere({ ...active, stage_label: 'Бухгалтерия', current_role: 'accounting' }), 'Бухгалтерия');
  assert.equal(registryWhere({ ...active, stage_label: 'Оплата' }), 'Оплата');
  assert.equal(registryWhere({ ...active, state: 'clarification', clarify_from: 'invoice_payment', current_assignee_name: 'Ерсултан Айгерим' }),
    'Требуется уточнение · Ерсултан Айгерим');
  // У закрытой заявки исполнителя нет.
  assert.equal(registryWhere({ status: 'done', current_assignee_name: 'Жумабаев Тимур' }), 'Закрыта');
  assert.equal(registryWhere({ status: 'rejected' }), 'Отклонена');
  assert.equal(registryWhere(null), '');
  // Бухгалтерия на «Согласовании» уточняет компанию — телефон говорит то же, что таблица.
  assert.equal(registryWhere({ ...active, stage_label: 'Согласование', current_role: 'accounting', current_kind: 'entity_clarify' }),
    'Уточнение компании · Бухгалтерия');
});

test('имя файла выгрузки', () => {
  assert.equal(exportFileName('2026-10-08'), 'Заявки на закуп и оплату 2026-10-08.xlsx');
});

/* ── Согласование и реквизиты ──────────────────────────────────────────────── */

test('маршрут согласования показывается, а не выбирается (п. 6)', () => {
  assert.equal(approvalSummary(null), null);
  const role = approvalSummary({ approver_name: null, basis: 'Любой из утверждающих', manager_step: true, manager_reason: 'новый закуп', trace: [] });
  assert.equal(role.approver, 'Любой из утверждающих');
  assert.equal(role.personal, false);
  assert.equal(role.basisText, '');
  assert.equal(role.managerStep, true);
  const personal = approvalSummary({ approver_name: 'Алиева Зарина', basis: 'По регулярному платежу «Аренда»', manager_step: false,
    trace: [{ ok: true, text: 'Регулярный платёж «Аренда»' }], conflict: true });
  assert.equal(personal.approver, 'Алиева Зарина');
  assert.equal(personal.basisText, 'По регулярному платежу «Аренда»');
  assert.equal(personal.managerStep, false);
  assert.equal(personal.conflict, true);
  assert.equal(personal.trace.length, 1);
});

test('реквизиты компании одним текстом — для «Скопировать» (п. 13.3)', () => {
  assert.equal(requisitesText(null), '');
  assert.equal(requisitesText({
    name: 'ТОО «Наше»', bin: '123456789012', legal_address: 'г. Алматы, пр. Абая, 10', iik: 'KZ11722S000012345678',
    bank_name: 'АО «Kaspi Bank»', bik: 'CASPKZKA', kbe: '17', vat_payer: true, requisites: ' Корр. счёт 123 ',
  }), ['ТОО «Наше»', 'БИН 123456789012', 'Юридический адрес: г. Алматы, пр. Абая, 10', 'ИИК KZ11722S000012345678',
    'Банк: АО «Kaspi Bank»', 'БИК CASPKZKA', 'КБЕ 17', 'Плательщик НДС', 'Корр. счёт 123'].join('\n'));
  assert.equal(requisitesText({ name: 'ИП Пример' }), 'ИП Пример\nБез НДС');
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

/* ── История (п. 18) ───────────────────────────────────────────────────────── */

test('история читается словами, а не кодами', () => {
  const titleOf = (kind) => ({ approval: 'Утверждение', invoice_payment: 'Оплата счёта' }[kind] || '');
  assert.equal(describeEvent({ kind: 'created' }), 'Заявка создана');
  assert.equal(describeEvent({ kind: 'card_revealed' }), 'Посмотрел полный номер карты');
  assert.equal(describeEvent({ kind: 'submitted', payload: { first: true } }), 'Отправлена на согласование');
  assert.equal(describeEvent({ kind: 'submitted', payload: { changed: ['сумма', 'поставщик'] } }),
    'Отправлена на согласование заново: изменено — сумма, поставщик');
  assert.equal(describeEvent({ kind: 'approved', payload: { kind: 'manager_approval' } }), 'Согласовано руководителем');
  assert.equal(describeEvent({ kind: 'approved', payload: { kind: 'approval' } }), 'Утверждено');
  assert.equal(describeEvent({ kind: 'returned', payload: { kind: 'approval' } }, titleOf), 'Возвращена на доработку — «Утверждение»');
  assert.equal(describeEvent({ kind: 'clarification', payload: { kind: 'invoice_payment' } }, titleOf), 'Запрошена информация — «Оплата счёта»');
  assert.equal(describeEvent({ kind: 'clarification', payload: { kind: 'invoice_payment', system: true } }, titleOf), 'Требуется уточнение — «Оплата счёта»');
  assert.equal(describeEvent({ kind: 'paid', payload: { paid_amount: 106000 } }), `Счёт оплачен: ${nb('106 000 ₸')}`);
  assert.equal(describeEvent({ kind: 'topped_up', payload: { paid_amount: 45000 } }), `Карта пополнена: ${nb('45 000 ₸')}`);
  assert.equal(describeEvent({ kind: 'receipt_provided' }), 'Приложен чек');
  assert.equal(describeEvent({ kind: 'received', payload: { received_quantity: '10 уп' } }), 'Получение подтверждено: 10 уп');
  assert.equal(describeEvent({ kind: 'asset_registered', payload: { inventory_numbers: ['ИНВ-1', 'ИНВ-2'] } }),
    'Имущество поставлено на учёт: ИНВ-1, ИНВ-2');
  assert.equal(describeEvent({ kind: 'docs_status', payload: { to: 'original' } }), 'Закрывающие документы: оригинал получен');
  assert.equal(describeEvent({ kind: 'closed' }), 'Заявка закрыта');
  assert.equal(describeEvent({ kind: 'reassigned', payload: { kind: 'approval', assignee_name: 'Сауле' } }, titleOf), 'Исполнитель «Утверждение»: Сауле');
  assert.equal(describeEvent({ kind: 'edited', payload: { changes: { amount: [1, 2], notes: ['', 'x'], offers: [3, 3] } } }),
    'Изменено: Сумма, Комментарий, Варианты поставщиков');
  assert.equal(describeEvent({ kind: 'attachment_added', payload: { file_name: 'счёт.pdf' } }), 'Файл добавлен: счёт.pdf');
  assert.equal(describeEvent({ kind: 'refund', payload: { refund_amount: 450 } }), `Отмечен возврат ${nb('450 ₸')}`);
  // События первой версии раздела остаются в истории старых заявок.
  assert.equal(describeEvent({ kind: 'step_done', step_no: 7 }), 'Шаг 7 — отписка');
  assert.equal(describeEvent({ kind: 'migrated' }), 'Переведена на этапы');
  // Бухгалтерия указала компанию, которой не знал инициатор (15.10.2026).
  assert.equal(describeEvent({ kind: 'entity_set', payload: { legal_entity_name: 'ТОО «Север»' } }), 'Указана компания: ТОО «Север»');
  assert.equal(describeEvent({ kind: 'entity_set', payload: {} }), 'Указана компания');
  // Без компании заявка сначала у бухгалтерии — так и записано в истории.
  assert.equal(describeEvent({ kind: 'submitted', payload: { first: true, entity_step: true } }), 'Отправлена: компанию уточнит бухгалтерия');
  assert.equal(describeEvent({ kind: 'submitted', payload: { first: false, entity_step: true, changed: ['компания'] } }),
    'Отправлена заново: изменено — компания; компанию уточнит бухгалтерия');
  assert.equal(describeEvent({ kind: 'submitted', payload: { first: false, entity_step: true } }), 'Отправлена заново: компанию уточнит бухгалтерия');
  assert.equal(describeEvent({ kind: 'submitted', payload: { first: true, entity_step: false } }), 'Отправлена на согласование');
  // Подсказка обязательного поля (заявка первой версии) не обещает «Не знаю».
  assert.ok(!HINTS.legalEntityRequired.options && !/Не знаю/.test(JSON.stringify(HINTS.legalEntityRequired)));
  assert.ok(HINTS.legalEntity.options.some(([name]) => name === 'Не знаете'));
});

/* ── Варианты выбора и подсказки ───────────────────────────────────────────── */

test('у каждой подсказки есть фраза «зачем поле», у вариантов — имя и смысл', () => {
  const keys = Object.keys(HINTS);
  assert.ok(keys.length >= 30);
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
    ['filterKind', KIND_SHORT_OPTIONS], ['limitKind', KIND_SHORT_OPTIONS],
    ['filterMethod', METHOD_SHORT_OPTIONS], ['limitMethod', METHOD_SHORT_OPTIONS], ['objectType', OBJECT_TYPE_OPTIONS],
    ['accountingCategory', ACCOUNTING_CATEGORY_OPTIONS], ['paymentMethod', PAYMENT_METHOD_OPTIONS],
    ['cardRecipient', CARD_RECIPIENT_OPTIONS], ['alternatives', ALTERNATIVES_OPTIONS],
    ['vatPayer', VAT_OPTIONS], ['partyKind', PARTY_KIND_OPTIONS], ['managerStep', MANAGER_STEP_OPTIONS],
    ['autoCreate', AUTO_CREATE_OPTIONS], ['assetStatus', ASSET_STATUS_OPTIONS],
  ];
  for (const [key, options] of pairs) {
    assert.deepEqual(
      HINTS[key].options.map(([name]) => name).sort(),
      options.map((option) => option.label).sort(),
      `${key}: подсказка и селектор разошлись`,
    );
  }
});

test('выбор из двух вариантов: «не выбрано» отличается от «нет»', () => {
  assert.deepEqual(VAT_OPTIONS.map((option) => option.value), [false, true]);
  assert.deepEqual(ALTERNATIVES_OPTIONS.map((option) => option.value), [false, true]);
  // Способов оплаты ровно два (п. 5), типов обращения — два (п. 4).
  assert.deepEqual(PAYMENT_METHOD_OPTIONS.map((option) => option.value), ['invoice', 'card']);
  assert.deepEqual(REQUEST_KIND_OPTIONS.map((option) => option.value), ['purchase', 'regular']);
  assert.deepEqual(CARD_RECIPIENT_OPTIONS.map((option) => option.value), ['employee', 'supplier']);
});

test('участники: четыре роли со списком, две из них обязательны для любой заявки', () => {
  assert.deepEqual(MEMBER_ROLES.map((role) => role.code), ['approver', 'accounting', 'finance', 'asset_keeper']);
  assert.deepEqual(MEMBER_ROLES.filter((role) => role.required).map((role) => role.code), ['approver', 'accounting']);
  for (const role of MEMBER_ROLES) assert.ok(role.hint.length > 20, role.code);
});

test('виды файлов по месту: первым стоит тот, что нужен этапу', () => {
  assert.equal(FILE_KINDS.invoice_payment[0], 'payment_order');
  assert.equal(FILE_KINDS.card_topup[0], 'transfer_proof');
  assert.equal(FILE_KINDS.receipt_confirm[0], 'receipt');
  assert.equal(FILE_KINDS.asset_registration[0], 'handover_act');
  assert.deepEqual(fileKindOptions('receipt_confirm')[0], { value: 'receipt', label: 'Чек' });
  for (const kinds of Object.values(FILE_KINDS)) {
    for (const option of kinds.map((kind) => fileKindOptions('x').find((item) => item.value === kind))) {
      assert.ok(option && option.label, 'у каждого вида файла есть подпись');
    }
  }
});

test('окно записи: первый кадр после открытия — уже загрузка, а не пустое окно', () => {
  // Запрос ещё не ушёл (loading = false), данных нет — окно стоит с заготовкой.
  // Пустое окно на этот кадр и было той «мини-карточкой», которая потом вырастала.
  assert.equal(recordView({ data: null, error: '', loading: false }), 'loading');
  assert.equal(recordView({ data: null, error: '', loading: true }), 'loading');
  assert.equal(recordView(), 'loading');
});

test('окно записи: отказ виден, только когда запрос закончился', () => {
  assert.equal(recordView({ data: null, error: 'Заявка не найдена', loading: false }), 'error');
  // Идёт новая попытка — прошлая ошибка не мелькает поверх заготовки.
  assert.equal(recordView({ data: null, error: 'Заявка не найдена', loading: true }), 'loading');
});

test('окно записи: загруженная запись остаётся на экране при перечитывании и при отказе действия', () => {
  const data = { request: { id: 7 } };
  assert.equal(recordView({ data, error: '', loading: false }), 'ready');
  assert.equal(recordView({ data, error: '', loading: true }), 'ready');
  assert.equal(recordView({ data, error: 'Этап уже выполнил другой', loading: false }), 'ready');
});

test('заготовка заявки не ниже окна на обычном мониторе и ростом с настоящую заявку', () => {
  const rowsOf = (section) => (typeof section === 'number' ? section : section.rows);
  // Строка заготовки — 32 px, как Row; раздел добавляет заголовок и поля карточки (38 px) и
  // промежуток (16 px); строка свёрнутого раздела — 44 px; сводка заявки, шапка, подвал и поля
  // окна — ещё около 300 px.
  const height = REQUEST_SKELETON.reduce((sum, section) => sum + rowsOf(section) * 32 + 38 + 16, 300)
    + CARD_FOLDS.length * 44 + 16;
  // Окно не выше 92% экрана: при экране 1080 px по высоте это 994 px — заготовка окно заполняет,
  // и рамка при загрузке не меняется вовсе.
  assert.ok(height >= 994, `заготовка заявки — около ${height} px`);
  // Заявка без формы этапа со свёрнутыми разделами — около 1250 px (замер на стенде, №515):
  // на высоком экране заготовка не должна быть заметно выше или ниже неё.
  assert.ok(Math.abs(height - 1250) <= 120, `заготовка заявки — около ${height} px при заявке около 1250 px`);
  assert.ok(REQUEST_SKELETON.every((section) => rowsOf(section) > 0));
  // Карточка имущества: описание без заголовка — одиннадцать обязательных полей п. 10.2
  // (название стоит в шапке окна) и строка «Заявка»; ниже — история владельцев и мест.
  assert.deepEqual(ASSET_SKELETON[0], { rows: 12, title: false });
  assert.equal(ASSET_SKELETON.length, 2);
});

test('разделы «по запросу»: ход заявки, наблюдатели, документы, история — в этом порядке', () => {
  assert.deepEqual(CARD_FOLDS.map((fold) => fold.key), ['route', 'observers', 'files', 'history']);
  assert.deepEqual(CARD_FOLDS.map((fold) => fold.title), ['Ход заявки', 'Наблюдатели', 'Документы', 'История']);
});

test('свёрнутый «Ход заявки» говорит, у кого заявка сейчас', () => {
  const subtasks = [
    { kind: 'initiation', state: 'done', title: 'Заявка у инициатора' },
    { kind: 'approval', state: 'done', title: 'Утверждение', assignee_name: 'Карабек Ержан' },
    { kind: 'invoice_payment', state: 'open', title: 'Оплата счёта', role_label: 'Бухгалтерия' },
    { kind: 'receiving', state: 'pending', title: 'Получение товара/услуги', assignee_name: 'Иванова Айгерим' },
  ];
  assert.equal(routeSummary({ status: 'active' }, subtasks), 'Оплата счёта · Бухгалтерия');
  // Человек важнее роли: этап согласования — у конкретного утверждающего.
  assert.equal(routeSummary({ status: 'active' }, [{ kind: 'approval', state: 'open', title: 'Утверждение', assignee_name: 'Карабек Ержан', role_label: 'Утвердитель' }]),
    'Утверждение · Карабек Ержан');
  assert.equal(routeSummary({ status: 'done' }, subtasks), 'Все этапы пройдены');
  assert.equal(routeSummary({ status: 'rejected' }, subtasks), 'Отклонена');
  assert.equal(routeSummary({ status: 'cancelled' }, subtasks), 'Отменена');
  // Открытого этапа нет (заявка между этапами) — хотя бы сколько их всего, без инициации и пропущенных.
  assert.equal(routeSummary({ status: 'active' }, [
    { kind: 'initiation', state: 'done' }, { kind: 'manager_approval', state: 'skipped' },
    { kind: 'approval', state: 'pending' }, { kind: 'invoice_payment', state: 'pending' },
  ]), '2 этапа');
  assert.equal(routeSummary(null, []), '');
});

test('свёрнутые «Документы» и «История» называют, сколько внутри', () => {
  assert.equal(filesSummary([]), 'нет файлов');
  assert.equal(filesSummary(undefined), 'нет файлов');
  assert.equal(filesSummary([{}]), '1 файл');
  assert.equal(filesSummary([{}, {}, {}]), '3 файла');
  assert.equal(filesSummary(new Array(11).fill({})), '11 файлов');
  assert.equal(historySummary([]), 'пока пусто');
  // Сервер отдаёт события по порядку записи — последнее в конце.
  const events = [{ created_at: '2026-10-09T12:24:00+05:00' }, { created_at: '2026-10-12T14:37:00+05:00' }];
  assert.equal(historySummary(events), `2 события · последнее ${fmtDateTime(events[1].created_at)}`);
  assert.match(historySummary(new Array(17).fill({ created_at: '2026-10-12T14:37:00+05:00' })), /^17 событий · последнее /);
});

test('доска: колонки делят ширину колонки раздела поровну, пока каждой хватает места', () => {
  // Монитор 2560 px: колонка раздела упирается в свой предел, поле доски — 2214 px. Восемь
  // колонок бухгалтерии стоят все, по 268 px, без прокрутки.
  const wide = boardLayout(2214, 8);
  assert.deepEqual(wide, { fits: true, width: 268, visible: 8 });
  assert.ok(wide.width * 8 + BOARD_COLUMN_GAP * 7 <= 2214);
  // Ровно впритык: колонка наименьшей ширины ещё считается «помещается».
  const exact = BOARD_COLUMN_MIN * 5 + BOARD_COLUMN_GAP * 4;
  assert.deepEqual(boardLayout(exact, 5), { fits: true, width: BOARD_COLUMN_MIN, visible: 5 });
  assert.equal(boardLayout(exact - 1, 5).fits, false);
  // Колонок мало — они всё равно занимают поле целиком: доска стоит с теми же краями, что
  // шапка, вкладки и поиск над ней. С пределом ширины колонки доска на пять колонок была уже
  // шапки, а без общей колонки — шире её; владелец 13.10.2026: «выглядит вообще не симметрично».
  for (const [area, count] of [[2214, 5], [2214, 7], [1720, 5], [1720, 7], [1240, 5], [1544, 3]]) {
    const layout = boardLayout(area, count);
    assert.equal(layout.fits, true, `${area}/${count}`);
    const used = layout.width * count + BOARD_COLUMN_GAP * (count - 1);
    assert.ok(used <= area && area - used < count, `${area}/${count}: занято ${used} из ${area}`);
  }
  assert.deepEqual(boardLayout(2214, 5), { fits: true, width: 434, visible: 5 });
});

test('доска: когда все не помещаются, следующая колонка выглядывает из-за края', () => {
  for (const [area, count] of [[1080, 8], [1166, 8], [1240, 8], [1312, 8], [1312, 7], [1480, 8], [1544, 8], [1720, 8], [1080, 5], [900, 8], [700, 7]]) {
    const layout = boardLayout(area, count);
    assert.equal(layout.fits, false, `${area}/${count}`);
    assert.ok(layout.width >= BOARD_COLUMN_MIN, `${area}/${count}: ширина ${layout.width}`);
    assert.ok(layout.visible >= 1 && layout.visible < count, `${area}/${count}: видно ${layout.visible}`);
    // Целые колонки стоят на экране полностью…
    const full = layout.visible * layout.width + layout.visible * BOARD_COLUMN_GAP;
    assert.ok(full <= area, `${area}/${count}: целые колонки ${full} шире поля`);
    // …а от следующей видна заметная часть: не узкая полоска и не почти вся колонка.
    const peek = (area - full) / layout.width;
    assert.ok(peek >= BOARD_COLUMN_PEEK - 0.02 && peek < 0.75, `${area}/${count}: выглядывает ${Math.round(peek * 100)}%`);
  }
  // Замеры стенда. Во вкладке браузера поле доски = экран − 200 px (меню 80, поля области
  // портала 64, поля раздела 56): 1920 px — шесть колонок бухгалтерии из восьми, 1512 px —
  // пять, 1366 px — четыре.
  assert.equal(boardLayout(1720, 8).visible, 6);
  assert.equal(boardLayout(1312, 8).visible, 5);
  assert.equal(boardLayout(1166, 8).visible, 4);
  // Экран владельца — 1680 px в установленном портале, где у области портала полей нет
  // (поле 1544 px): тоже шесть.
  assert.equal(boardLayout(1544, 8).visible, 6);
  // А семь колонок финансового отдела и пять согласования на 1920 px стоят целиком.
  assert.equal(boardLayout(1720, 7).fits, true);
  assert.equal(boardLayout(1720, 5).fits, true);
});

test('доска: пустая и ещё не измеренная', () => {
  assert.deepEqual(boardLayout(1500, 0), { fits: true, width: BOARD_COLUMN_MIN, visible: 0 });
  assert.equal(boardLayout(0, 8).fits, true);
  assert.equal(boardLayout(undefined, 8).fits, true);
  // Очень узкое поле — одна колонка, но не уже наименьшей.
  assert.deepEqual(boardLayout(200, 3), { fits: false, width: BOARD_COLUMN_MIN, visible: 1 });
});

test('доска: метка у края называет колонки, которых не видно целиком', () => {
  const columns = ['new', 'checking', 'clarification', 'ready', 'paying', 'paid', 'awaiting_docs', 'closed'].map((key) => ({ key }));
  const layout = boardLayout(1720, columns.length);                 // шесть целых и выглядывающая седьмая
  const keys = (list) => list.map((column) => column.key);
  let hidden = boardHidden(columns, layout, 0, 1720);
  assert.deepEqual(keys(hidden.left), []);
  assert.deepEqual(keys(hidden.right), ['awaiting_docs', 'closed']);
  // Долистали до конца: справа пусто, слева — ушедшие за край.
  const content = columns.length * layout.width + (columns.length - 1) * BOARD_COLUMN_GAP;
  hidden = boardHidden(columns, layout, content - 1720, 1720);
  assert.deepEqual(keys(hidden.right), []);
  assert.deepEqual(keys(hidden.left), ['new', 'checking']);
  // Дробная прокрутка на пиксель колонку «скрытой» не делает.
  hidden = boardHidden(columns, layout, 1, 1720);
  assert.deepEqual(keys(hidden.left), []);
  // Всё помещается — меток нет.
  const fit = boardLayout(2214, columns.length);
  assert.deepEqual(boardHidden(columns, fit, 0, 2214), { left: [], right: [] });
});

test('доска: кнопка «ещё» листает на видимые колонки без одной', () => {
  const layout = boardLayout(1720, 8);
  assert.equal(boardPageStep(layout), (layout.visible - 1) * (layout.width + BOARD_COLUMN_GAP));
  // Одна видимая колонка — листаем по одной, а не стоим на месте.
  assert.equal(boardPageStep({ visible: 1, width: 232 }), 232 + BOARD_COLUMN_GAP);
});

test('доска: ползунок тонкой полосы показывает видимую долю колонок', () => {
  // Видно 1744 из 2182 — ползунок занимает ту же долю дорожки.
  let thumb = boardThumb(0, 2182, 1744, 1736);
  assert.equal(thumb.left, 0);
  assert.equal(thumb.width, Math.round((1744 / 2182) * 1736));
  // В конце прокрутки ползунок упирается в правый край дорожки.
  thumb = boardThumb(2182 - 1744, 2182, 1744, 1736);
  assert.equal(thumb.left + thumb.width, 1736);
  // Листать нечего — ползунка нет.
  assert.equal(boardThumb(0, 1744, 1744, 1736), null);
  assert.equal(boardThumb(0, 2182, 1744, 0), null);
  // Очень длинная доска — ползунок не тоньше, чем можно ухватить.
  assert.equal(boardThumb(0, 100000, 1000, 1000).width, BOARD_THUMB_MIN);
});

test('доска: ползунок и прокрутка переводятся друг в друга без потерь на краях', () => {
  const content = 2182, view = 1744, track = 1736;
  const thumb = boardThumb(0, content, view, track);
  assert.equal(boardScrollFor(0, thumb.width, content, view, track), 0);
  assert.equal(boardScrollFor(track - thumb.width, thumb.width, content, view, track), content - view);
  // За края дорожки ползунок не уезжает.
  assert.equal(boardScrollFor(-50, thumb.width, content, view, track), 0);
  assert.equal(boardScrollFor(track * 2, thumb.width, content, view, track), content - view);
  // Середина дорожки — середина прокрутки.
  const middle = boardScrollFor((track - thumb.width) / 2, thumb.width, content, view, track);
  assert.ok(Math.abs(middle - (content - view) / 2) <= 1);
  assert.equal(boardScrollFor(10, track, content, view, track), 0);
});
