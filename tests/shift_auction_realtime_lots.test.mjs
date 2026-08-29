/*
 * Патч лота аукциона по событию реального времени.
 *
 * Событие несёт ОДИН кусок смены, а список кусков (claim_segments) приезжает
 * только в полном снапшоте — и грузится он лишь тем, кого событие касается.
 * Значит список правит патч, иначе картина у операторов разъезжается: взятая
 * коллегой часть не появится, а возвращённая не исчезнет.
 *
 * Отдельно проверяем стыковку с отбором своих смен: сегмент без стадии там
 * считается добором, а добор возвращать нельзя — то есть неверная стадия в
 * патче молча отобрала бы у человека кнопку «Вернуть».
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { mergeRealtimeAuctionLot } from '../src/components/resources/shiftAuctionRealtimeLots.js';
import { collectMyAuctionDayClaims } from '../src/components/resources/shiftAuctionDayClaims.js';

const DATE = '2026-09-01';
const ME = 77;
const OTHER = 999;

const lot = (over) => ({
  id: 5, shift_date: DATE, start_time: '09:00', end_time: '21:00',
  status: 'available', claimed_by: null, claim_segments: [], ...over,
});

const segment = (over) => ({
  claimed_by: OTHER, claimed_by_name: 'Коллега',
  start_time: '09:00', end_time: '15:00', stage: 'auction', ...over,
});

test('взятая в аукционе часть добавляется соседям в claim_segments', () => {
  const merged = mergeRealtimeAuctionLot(
    lot(),
    {
      id: 5, status: 'available', claimed_by: OTHER, claimed_by_name: 'Коллега',
      claim_start_time: '09:00', claim_end_time: '15:00',
      partial: true, lot_still_available: true,
    },
    'lot_claimed',
    { lot: {} },
  );

  assert.equal(merged.claim_segments.length, 1);
  assert.equal(merged.claim_segments[0].claimed_by, OTHER);
  assert.equal(merged.claim_segments[0].start_time, '09:00');
  assert.equal(merged.claim_segments[0].end_time, '15:00');
  // Стадия обязательна: без неё отбор своих смен посчитает кусок добором.
  assert.equal(merged.claim_segments[0].stage, 'auction');
});

test('свою часть, приехавшую событием, можно вернуть — стадия доезжает до отбора', () => {
  const merged = mergeRealtimeAuctionLot(
    lot(),
    {
      id: 5, status: 'available', claimed_by: ME,
      claim_start_time: '15:00', claim_end_time: '21:00', partial: true,
    },
    'lot_claimed',
    {},
  );

  const rows = collectMyAuctionDayClaims({ lots: [merged], date: DATE, userId: ME });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].lot?.id, 5, 'часть из аукциона обязана остаться возвращаемой');
  assert.equal(rows[0].claimLot.claim_start_time, '15:00');
});

test('целиком взятая смена линии сегментов не заводит', () => {
  const merged = mergeRealtimeAuctionLot(
    lot(),
    { id: 5, status: 'claimed', claimed_by: OTHER },
    'lot_claimed',
    {},
  );

  assert.deepEqual(merged.claim_segments, []);
  assert.equal(merged.status, 'claimed');
});

test('возврат части убирает ровно её, чужие куски остаются', () => {
  const current = lot({
    claim_segments: [
      segment({ claimed_by: OTHER, start_time: '09:00', end_time: '15:00' }),
      segment({ claimed_by: ME, start_time: '15:00', end_time: '21:00' }),
    ],
  });

  const merged = mergeRealtimeAuctionLot(
    current,
    {
      id: 5, status: 'available', claimed_by: null,
      claim_start_time: '15:00', claim_end_time: '21:00', partial: true,
    },
    'lot_released',
    // У возврата лот уже обезличен — автора несёт payload.
    { operator_id: ME },
  );

  assert.equal(merged.claim_segments.length, 1);
  assert.equal(merged.claim_segments[0].claimed_by, OTHER);
  // И вернувший больше не видит эту смену своей.
  assert.deepEqual(collectMyAuctionDayClaims({ lots: [merged], date: DATE, userId: ME }), []);
});

test('возврат целой смены сегменты не трогает', () => {
  const current = lot({
    status: 'claimed',
    claimed_by: ME,
    claim_segments: [segment({ claimed_by: OTHER })],
  });

  const merged = mergeRealtimeAuctionLot(
    current,
    { id: 5, status: 'available', claimed_by: null, partial: false },
    'lot_released',
    { operator_id: ME },
  );

  assert.equal(merged.claim_segments.length, 1);
  assert.equal(merged.status, 'available');
});

test('пост-аукционный добор по-прежнему добавляется своей стадией', () => {
  const merged = mergeRealtimeAuctionLot(
    lot(),
    {
      id: 5, status: 'available',
      claim_start_time: '18:00', claim_end_time: '21:00',
    },
    'lot_post_auction_claimed',
    { operator_id: ME, operator_name: 'Я' },
  );

  assert.equal(merged.claim_segments.length, 1);
  assert.equal(merged.claim_segments[0].stage, 'post_auction');
  assert.equal(merged.claim_segments[0].claimed_by_name, 'Я');
  // Добор возвращать нельзя — отбор своих смен обязан оставить lot пустым.
  const rows = collectMyAuctionDayClaims({ lots: [merged], date: DATE, userId: ME });
  assert.equal(rows[0].lot, null);
});

test('повторное событие не задваивает кусок', () => {
  const incoming = {
    id: 5, status: 'available', claimed_by: OTHER,
    claim_start_time: '09:00', claim_end_time: '15:00', partial: true,
  };
  const once = mergeRealtimeAuctionLot(lot(), incoming, 'lot_claimed', {});
  const twice = mergeRealtimeAuctionLot(once, incoming, 'lot_claimed', {});

  assert.equal(twice.claim_segments.length, 1);
});

test('событие без границ куска список не портит', () => {
  const current = lot({ claim_segments: [segment()] });
  const merged = mergeRealtimeAuctionLot(
    current,
    { id: 5, status: 'available', partial: true },
    'lot_released',
    { operator_id: ME },
  );

  assert.deepEqual(merged.claim_segments, current.claim_segments);
});
