import test from 'node:test';
import assert from 'node:assert/strict';

import {
  STALE_AFTER_DAYS,
  daysInOffice,
  describeEvent,
  extractAccountId,
  fmtDate,
  fmtPhone,
  isClosed,
  isStale,
  officeChoiceFor,
  pluralDays,
  statusMeta,
} from '../src/components/parcels/parcelMeta.js';

/* Правила раздела «Посылки», которые раньше были бы разметкой.
 *
 * Главное здесь — разбор ссылки на аккаунт водителя. В живой ссылке Флита ДВА
 * 32-значных значения, и первым идёт таксопарк: наивное «первое совпадение»
 * записало бы в карточку чужого человека, и в реестре этого никто бы не заметил.
 * Поэтому у каждого вида ссылки свой тест. */

// Живые ссылки, присланные владельцем 25.08.2026. Водитель в обеих один и тот же.
const LINK_WITH_PARAMS = 'https://fleet.yandex.kz/contractors?park_id=cb1562e507f34940bef13b8d19a9221b&contractor_id=9b139a9dbe8d49bfbf8521b619c89198&candidate_id=b4df0290-2759-47e5-9920-c4494a4e4f05';
const LINK_WITH_PATH = 'https://fleet.yandex.kz/contractors/9b139a9dbe8d49bfbf8521b619c89198/details?park_id=cb1562e507f34940bef13b8d19a9221b';
const DRIVER_ID = '9b139a9dbe8d49bfbf8521b619c89198';
const PARK_ID = 'cb1562e507f34940bef13b8d19a9221b';

test('из ссылки с параметрами берётся водитель, а не таксопарк', () => {
  assert.equal(extractAccountId(LINK_WITH_PARAMS), DRIVER_ID);
  assert.notEqual(extractAccountId(LINK_WITH_PARAMS), PARK_ID);
});

test('из ссылки с id в пути берётся водитель, хотя park_id стоит в запросе', () => {
  assert.equal(extractAccountId(LINK_WITH_PATH), DRIVER_ID);
});

test('голый id принимается как есть и приводится к нижнему регистру', () => {
  assert.equal(extractAccountId(DRIVER_ID), DRIVER_ID);
  assert.equal(extractAccountId(DRIVER_ID.toUpperCase()), DRIVER_ID);
  assert.equal(extractAccountId(`  ${DRIVER_ID}  `), DRIVER_ID);
});

test('ссылка без схемы разбирается — её копируют из адресной строки', () => {
  assert.equal(extractAccountId(`fleet.yandex.kz/contractors/${DRIVER_ID}/details`), DRIVER_ID);
});

test('старый вид ссылки Флита и админка yataxi тоже разбираются', () => {
  assert.equal(extractAccountId(`https://fleet.yandex.ru/drivers/${DRIVER_ID}/card`), DRIVER_ID);
  assert.equal(
    extractAccountId(`https://backend.yataxi.kz/admin/driver-accounts/${DRIVER_ID}`),
    DRIVER_ID,
  );
});

test('ссылка, в которой водителя нет, отвечает отказом, а не угадывает парк', () => {
  assert.equal(extractAccountId(`https://fleet.yandex.kz/parks?park_id=${PARK_ID}`), null);
  assert.equal(extractAccountId('https://fleet.yandex.kz/contractors'), null);
});

test('мусор во вводе не роняет разбор', () => {
  assert.equal(extractAccountId(''), null);
  assert.equal(extractAccountId(null), null);
  assert.equal(extractAccountId('   '), null);
  assert.equal(extractAccountId('не ссылка вовсе'), null);
  assert.equal(extractAccountId('123456'), null);
});

/* ── Город → офис ───────────────────────────────────────────────────────── */

const OFFICES = [
  { id: 1, city: 'Алматы', name: 'Офис Алматы №1', address: 'Жамбыла 172В' },
  { id: 2, city: 'Алматы', name: 'Офис Алматы №2', address: '7-й микрорайон, 5' },
  { id: 3, city: 'Тараз', name: 'Офис Тараз', address: 'Казыбек би 138' },
];

test('в городе с одним офисом офис не спрашивается, а подставляется', () => {
  const choice = officeChoiceFor(OFFICES, 'Тараз');
  assert.equal(choice.asks, false);
  assert.equal(choice.autoOfficeId, 3);
});

test('в городе с несколькими офисами офис спрашивается и не подставляется', () => {
  const choice = officeChoiceFor(OFFICES, 'Алматы');
  assert.equal(choice.asks, true);
  assert.equal(choice.autoOfficeId, null);
  assert.equal(choice.options.length, 2);
});

test('город сверяется без учёта регистра и пробелов', () => {
  assert.equal(officeChoiceFor(OFFICES, '  тараз ').autoOfficeId, 3);
});

test('город без офисов не даёт ни выбора, ни подстановки', () => {
  const choice = officeChoiceFor(OFFICES, 'Актау');
  assert.equal(choice.asks, false);
  assert.equal(choice.autoOfficeId, null);
});

/* ── Сколько лежит ──────────────────────────────────────────────────────── */

test('дни считаются от даты приёма, у переданной посылки не считаются вовсе', () => {
  const lying = { received_on: '2026-08-01', status: 'in_office' };
  assert.equal(daysInOffice(lying, '2026-08-25'), 24);
  assert.equal(daysInOffice({ ...lying, status: 'given_to_recipient' }, '2026-08-25'), null);
});

test('дата приёма сегодня — ноль дней, а не пропуск', () => {
  assert.equal(daysInOffice({ received_on: '2026-08-25', status: 'in_office' }, '2026-08-25'), 0);
});

test('залежавшейся посылка становится ровно на пороге, не раньше', () => {
  const at = (days) => {
    const from = new Date(Date.UTC(2026, 7, 25) - days * 86400000);
    return { received_on: from.toISOString().slice(0, 10), status: 'in_office' };
  };
  assert.equal(isStale(at(STALE_AFTER_DAYS - 1), '2026-08-25'), false);
  assert.equal(isStale(at(STALE_AFTER_DAYS), '2026-08-25'), true);
});

test('битая дата не превращается в отрицательные дни', () => {
  assert.equal(daysInOffice({ received_on: '', status: 'in_office' }, '2026-08-25'), null);
  assert.equal(daysInOffice({ received_on: '2026-13-40', status: 'in_office' }, '2026-08-25'), null);
});

test('дни склоняются', () => {
  assert.equal(pluralDays(1), '1 день');
  assert.equal(pluralDays(2), '2 дня');
  assert.equal(pluralDays(5), '5 дней');
  assert.equal(pluralDays(11), '11 дней');
  assert.equal(pluralDays(21), '21 день');
  assert.equal(pluralDays(0), '0 дней');
});

/* ── Показ ──────────────────────────────────────────────────────────────── */

test('статус «В офисе» нейтральный, переданные — приглушённые', () => {
  assert.equal(statusMeta('in_office').tone, null);
  assert.equal(statusMeta('given_to_recipient').tone, 'muted');
  assert.equal(statusMeta('given_to_sender').tone, 'muted');
  assert.equal(isClosed('in_office'), false);
  assert.equal(isClosed('given_to_sender'), true);
});

test('незнакомый статус не роняет строку', () => {
  assert.equal(statusMeta('нечто').label, 'нечто');
  assert.equal(statusMeta(null).label, '—');
});

test('телефон из CRM показывается группами, чужой формат остаётся как есть', () => {
  assert.equal(fmtPhone('+77719736925'), '+7 771 973 69 25');
  assert.equal(fmtPhone('87719736925'), '+8 771 973 69 25');
  assert.equal(fmtPhone('внутренний 205'), 'внутренний 205');
  assert.equal(fmtPhone(''), null);
});

test('дата показывается по-русски, а битая — прочерком', () => {
  assert.equal(fmtDate('2026-08-01'), '1 авг 2026');
  assert.equal(fmtDate('2026-08-01T10:00:00'), '1 авг 2026');
  assert.equal(fmtDate(''), '—');
});

test('история читается словами, а не кодами событий', () => {
  assert.equal(describeEvent({ kind: 'created' }), 'Посылка добавлена в реестр');
  assert.equal(
    describeEvent({ kind: 'status', payload: { from: 'in_office', to: 'given_to_recipient' } }),
    'Статус изменён: В офисе → Передали получателю',
  );
  assert.equal(
    describeEvent({ kind: 'edited', payload: { changes: [{ label: 'Описание' }, { label: 'Получатель' }] } }),
    'Изменено: Описание, Получатель',
  );
  assert.equal(describeEvent({ kind: 'edited', payload: {} }), 'Карточка изменена');
  assert.equal(describeEvent({ kind: 'нечто' }), 'Изменение карточки');
});

test('комментарий без смены статуса не выдаёт себя за смену статуса', () => {
  assert.equal(describeEvent({ kind: 'comment', payload: { comment: 'лежит на месте' } }),
    'Добавлен комментарий');
  // Такие строки мог оставить прежний сервер: «В офисе → В офисе» — неправда.
  assert.equal(describeEvent({ kind: 'status', payload: { from: 'in_office', to: 'in_office' } }),
    'Статус подтверждён');
});
