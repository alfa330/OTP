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
  AUCTION_PHONE_ROW_KIND as KIND,
  classifyAuctionLotForPhone,
  describeAuctionPhoneDay,
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
