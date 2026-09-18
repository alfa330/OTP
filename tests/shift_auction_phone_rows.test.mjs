/*
 * Строка смены в телефонном виде «Аукциона смен».
 *
 * Порядок веток обязан совпадать с ячейкой сетки на компьютере (AuctionLotCell):
 * иначе телефон предложит «Взять» там, где сетка серая, и человек получит отказ
 * уже нажатием. Здесь закреплён каждый поворот этой лестницы.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  AUCTION_PHONE_CELL_ACTION as ACTION,
  AUCTION_PHONE_ROW_KIND as KIND,
  buildAuctionPhoneDayTimelineParts,
  buildAuctionPhoneGridRows,
  classifyAuctionLotForPhone,
  describeAuctionPhoneDay,
  pickAuctionPhoneCellAction,
} from '../src/components/resources/shiftAuctionPhoneRows.js';

const ME = 4;
const OTHER = 3;

const lot = (over) => ({
  id: 10, shift_date: '2026-09-15', start_time: '09:00', end_time: '18:00',
  status: 'available', claimed_by: null, post_auction_claimed: false, ...over,
});

test('свободная смена в открытом аукционе — «Взять»', () => {
  assert.deepEqual(
    classifyAuctionLotForPhone({ lot: lot(), userId: ME, canClaim: true }),
    { kind: KIND.TAKE, reason: '' },
  );
});

test('правило не пускает — строка без кнопки, с причиной словами', () => {
  const row = classifyAuctionLotForPhone({
    lot: lot(), userId: ME, canClaim: true, blockReason: 'На этот день уже выбрана смена',
  });
  assert.equal(row.kind, KIND.BLOCKED);
  assert.equal(row.reason, 'На этот день уже выбрана смена');
});

test('до старта, на паузе и в итогах смену видно, но взять нельзя', () => {
  assert.equal(classifyAuctionLotForPhone({ lot: lot(), userId: ME, canClaim: false }).kind, KIND.CLOSED);
});

test('взятая смена — моя или коллеги, по claimed_by', () => {
  assert.equal(classifyAuctionLotForPhone({ lot: lot({ status: 'claimed', claimed_by: ME }), userId: ME, canClaim: true }).kind, KIND.MINE);
  assert.equal(classifyAuctionLotForPhone({ lot: lot({ status: 'claimed', claimed_by: OTHER }), userId: ME, canClaim: true }).kind, KIND.TAKEN);
  // claimed_by строкой из JSON — всё равно моя.
  assert.equal(classifyAuctionLotForPhone({ lot: lot({ status: 'claimed', claimed_by: '4' }), userId: ME }).kind, KIND.MINE);
});

test('руководитель ничего не берёт: свободная смена у него просто свободна', () => {
  assert.equal(classifyAuctionLotForPhone({ lot: lot(), userId: ME, canManage: true, canClaim: true }).kind, KIND.FREE);
  assert.equal(
    classifyAuctionLotForPhone({ lot: lot(), userId: ME, canManage: true, postAuctionActive: true }).kind,
    KIND.FREE,
  );
});

test('после аукциона не начавшаяся свободная смена — «Добрать»', () => {
  const base = { lot: lot(), userId: ME, postAuctionActive: true };
  assert.equal(classifyAuctionLotForPhone(base).kind, KIND.TOPUP);
  assert.equal(classifyAuctionLotForPhone({ ...base, postAuctionOption: { canClaim: true } }).kind, KIND.TOPUP);
  // Отменённая смена в доборе берётся так же, как свободная.
  assert.equal(classifyAuctionLotForPhone({ ...base, lot: lot({ status: 'cancelled' }) }).kind, KIND.TOPUP);
});

test('добор без свободного интервала — серая строка с той же подписью, что у раздела', () => {
  const row = classifyAuctionLotForPhone({
    lot: lot(), userId: ME, postAuctionActive: true, postAuctionOption: { canClaim: false },
  });
  assert.deepEqual(row, { kind: KIND.BLOCKED, reason: 'Нет свободного интервала без пересечения' });
  // Причина раздела старше: она точнее общей подписи.
  assert.equal(
    classifyAuctionLotForPhone({
      lot: lot(), userId: ME, postAuctionActive: true, blockReason: 'День закрыт: Отпуск',
    }).reason,
    'День закрыт: Отпуск',
  );
});

test('начавшуюся смену добором не предлагают', () => {
  const row = classifyAuctionLotForPhone({ lot: lot(), userId: ME, postAuctionActive: true, hasStarted: true });
  assert.equal(row.kind, KIND.CLOSED);
});

test('уже взятая добором смена в доборе не повторяется', () => {
  const row = classifyAuctionLotForPhone({
    lot: lot({ status: 'claimed', claimed_by: OTHER, post_auction_claimed: true }),
    userId: ME,
    postAuctionActive: true,
  });
  assert.equal(row.kind, KIND.TAKEN);
});

test('без смены — без строки', () => {
  assert.equal(classifyAuctionLotForPhone({ lot: null }), null);
});

test('полоса дней: цвет только у своей смены, выходного и закрытого дня', () => {
  assert.deepEqual(describeAuctionPhoneDay({ state: 'shift' }, { shiftLabel: '9-18' }), { caption: '9-18', tone: 'shift' });
  assert.deepEqual(describeAuctionPhoneDay({ state: 'off' }), { caption: 'вых.', tone: 'off' });
  assert.deepEqual(describeAuctionPhoneDay({ state: 'blocked', blockedLabel: 'Отпуск' }), { caption: 'Отпуск', tone: 'blocked' });
  assert.deepEqual(describeAuctionPhoneDay({ state: 'available', available: 5 }), { caption: '5 св.', tone: 'none' });
  assert.deepEqual(describeAuctionPhoneDay({ state: 'locked', available: 0 }), { caption: '—', tone: 'none' });
});

test('полоса дней руководителя — сколько взято из скольких', () => {
  assert.deepEqual(describeAuctionPhoneDay({ claimed: 3, total: 9 }, { canMonitor: true }), { caption: '3/9', tone: 'none' });
  assert.deepEqual(describeAuctionPhoneDay({ claimed: 9, total: 9 }, { canMonitor: true }), { caption: '9/9', tone: 'done' });
  assert.deepEqual(describeAuctionPhoneDay({ claimed: 0, total: 0 }, { canMonitor: true }), { caption: '', tone: 'none' });
});

/*
 * Лента дня над «Моими сменами» и подсветка дня в полосе, пока неделю листают.
 */

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} ≠ ${expected}`);

test('лента дня: дневная смена ложится своими часами, перерыв — долей внутри полосы', () => {
  const [part] = buildAuctionPhoneDayTimelineParts({
    date: '2026-09-15',
    entries: [{ key: '7', date: '2026-09-15', start: 540, end: 1080, breaks: [{ start: 780, end: 810 }] }],
  });
  near(part.left, (540 / 1440) * 100);
  near(part.width, (540 / 1440) * 100);
  assert.equal(part.breaks.length, 1);
  near(part.breaks[0].left, ((780 - 540) / 540) * 100);
  near(part.breaks[0].width, (30 / 540) * 100);
});

test('ночь 20*08: вечер на ленте своего дня, хвост до 08:00 — на ленте следующего', () => {
  // Перерывы ночи приходят минутами от полуночи даты смены и уходят за 1440.
  const night = { key: '9', date: '2026-09-14', start: 1200, end: 1920, breaks: [{ start: 1340, end: 1355 }, { start: 1475, end: 1505 }] };
  const [evening] = buildAuctionPhoneDayTimelineParts({ date: '2026-09-14', entries: [night] });
  assert.equal(evening.startMin, 1200);
  assert.equal(evening.endMin, 1440);
  assert.equal(evening.breaks.length, 1, 'перерыв после полуночи — уже не на этой ленте');

  const [tail] = buildAuctionPhoneDayTimelineParts({ date: '2026-09-15', entries: [night] });
  assert.equal(tail.startMin, 0);
  assert.equal(tail.endMin, 480);
  assert.equal(tail.breaks.length, 1);
  near(tail.breaks[0].left, (35 / 480) * 100);

  assert.deepEqual(buildAuctionPhoneDayTimelineParts({ date: '2026-09-16', entries: [night] }), []);
});

test('лента дня: чужие даты и битые смены не рисуются, полосы идут по времени', () => {
  const parts = buildAuctionPhoneDayTimelineParts({
    date: '2026-09-15',
    entries: [
      { key: 'b', date: '2026-09-15', start: 900, end: 1080 },
      { key: 'a', date: '2026-09-15', start: 540, end: 840 },
      { key: 'gone', date: '2026-09-13', start: 540, end: 1080 },
      { key: 'bad', date: '2026-09-15', start: 600, end: 600 },
    ],
  });
  assert.deepEqual(parts.map((part) => part.key), ['a@2026-09-15', 'b@2026-09-15']);
  assert.deepEqual(buildAuctionPhoneDayTimelineParts({ date: '', entries: [{ key: 'x', date: '', start: 0, end: 60 }] }), []);
});

/*
 * Сетка недели: строки ставки и что делает нажатие на ячейку.
 */

test('сетка: i-я строка — i-я смена каждого дня, короткий день добит пустыми клетками', () => {
  const a = { id: 1 };
  const b = { id: 2 };
  const c = { id: 3 };
  const rows = buildAuctionPhoneGridRows(new Map([['d1', [a, b]], ['d2', [c]], ['d3', []]]), ['d1', 'd2', 'd3']);
  assert.deepEqual(rows, [[a, c, null], [b, null, null]]);
  assert.deepEqual(buildAuctionPhoneGridRows(new Map(), ['d1']), []);
  // День, которого нет в карте, — тоже пустая клетка, а не падение.
  assert.deepEqual(buildAuctionPhoneGridRows(new Map([['d1', [a]]]), ['d0', 'd1']), [[null, a]]);
});

test('нажатие на свободную ячейку: линия — лист с «Взять», чат и добор — сразу экран выбора', () => {
  assert.equal(pickAuctionPhoneCellAction({ kind: KIND.TAKE }), ACTION.CONFIRM);
  assert.equal(pickAuctionPhoneCellAction({ kind: KIND.TAKE, supportsPartialClaim: true }), ACTION.PARTIAL);
  assert.equal(pickAuctionPhoneCellAction({ kind: KIND.TOPUP }), ACTION.TOPUP);
  assert.equal(pickAuctionPhoneCellAction({ kind: KIND.TOPUP, supportsPartialClaim: true }), ACTION.TOPUP);
});

test('нажатие на свою ячейку: вернуть можно — лист «Вернуть», нельзя — экран дня', () => {
  assert.equal(pickAuctionPhoneCellAction({ kind: KIND.MINE, releasable: true }), ACTION.RELEASE);
  assert.equal(pickAuctionPhoneCellAction({ kind: KIND.MINE }), ACTION.DAY);
});

test('серая и закрытая ячейка объясняют, руководитель смотрит, кто взял', () => {
  for (const kind of [KIND.BLOCKED, KIND.CLOSED, KIND.TAKEN]) {
    assert.equal(pickAuctionPhoneCellAction({ kind }), ACTION.INFO, kind);
  }
  for (const kind of Object.values(KIND)) {
    assert.equal(pickAuctionPhoneCellAction({ kind, canManage: true, supportsPartialClaim: true, releasable: true }), ACTION.DETAILS, kind);
  }
});

/*
 * Взятая в ходе аукциона ЧАСТЬ смены (так разбирает смены чат).
 *
 * Лот при этом НАМЕРЕННО остаётся `available` с пустым `claimed_by` — иначе
 * оставшийся кусок пропал бы у остальных, — поэтому по статусу такую смену
 * своей не признать. Без этой ветки сетка на телефоне звала «взять» смену,
 * которую человек уже держит, а вернуть свой кусок из недели было нечем:
 * «Вернуть» оставалось только на экране дня, куда без подсказки не заходят.
 */

const part = (over) => ({ claimed_by: ME, start_time: '09:00', end_time: '12:00', stage: 'auction', ...over });

test('мой кусок делает смену моей, хотя лот остался свободным', () => {
  const row = classifyAuctionLotForPhone({
    lot: lot({ claim_segments: [part()] }), userId: ME, canClaim: true,
  });
  assert.deepEqual(row, { kind: KIND.MINE_PART, reason: '' });
});

test('смену закрыл коллега, но мой кусок в ней остаётся моим', () => {
  const row = classifyAuctionLotForPhone({
    lot: lot({ status: 'claimed', claimed_by: OTHER, claim_segments: [part(), part({ claimed_by: OTHER, start_time: '12:00', end_time: '18:00' })] }),
    userId: ME,
    canClaim: true,
  });
  assert.equal(row.kind, KIND.MINE_PART);
  // Взятая мной ЦЕЛИКОМ смена остаётся обычной «моей»: там есть claimed_by.
  assert.equal(classifyAuctionLotForPhone({
    lot: lot({ status: 'claimed', claimed_by: ME, claim_segments: [part()] }), userId: ME,
  }).kind, KIND.MINE);
});

test('чужой кусок своей смену не делает, мой добор — тоже', () => {
  assert.equal(classifyAuctionLotForPhone({
    lot: lot({ claim_segments: [part({ claimed_by: OTHER })] }), userId: ME, canClaim: true,
  }).kind, KIND.TAKE);
  // Добор уже в графике: его возвращают в «Моих доп. сменах», а не тут.
  assert.equal(classifyAuctionLotForPhone({
    lot: lot({ claim_segments: [part({ stage: 'post_auction' })] }), userId: ME, canClaim: true,
  }).kind, KIND.TAKE);
  // Стадии нет вовсе — это старая строка добора, стадия у них по умолчанию.
  assert.equal(classifyAuctionLotForPhone({
    lot: lot({ claim_segments: [part({ stage: undefined })] }), userId: ME, canClaim: true,
  }).kind, KIND.TAKE);
});

test('свой кусок старше причины отказа: она про «взять ещё», а не про возврат', () => {
  const row = classifyAuctionLotForPhone({
    lot: lot({ claim_segments: [part()] }), userId: ME, canClaim: true,
    blockReason: 'Больше нормы взять нельзя',
  });
  assert.equal(row.kind, KIND.MINE_PART);
});

test('в доборе смена с моим куском по-прежнему предлагается добрать', () => {
  const row = classifyAuctionLotForPhone({
    lot: lot({ claim_segments: [part()] }), userId: ME, postAuctionActive: true,
  });
  // Возврат в добор закрыт (аукцион уже не open) — ветка обязана остаться прежней.
  assert.equal(row.kind, KIND.TOPUP);
});

test('руководителю смена с чужими кусками остаётся свободной', () => {
  assert.equal(classifyAuctionLotForPhone({
    lot: lot({ claim_segments: [part()] }), userId: ME, canManage: true, canClaim: true,
  }).kind, KIND.FREE);
});

test('нажатие на свой кусок: лист с возвратом и добором, а без права возврата — день', () => {
  assert.equal(pickAuctionPhoneCellAction({ kind: KIND.MINE_PART, releasable: true }), ACTION.PART);
  assert.equal(pickAuctionPhoneCellAction({ kind: KIND.MINE_PART }), ACTION.DAY);
});
