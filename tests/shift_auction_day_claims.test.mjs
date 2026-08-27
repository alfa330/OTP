/*
 * Отбор собственных смен оператора на день аукциона.
 *
 * Проверяем настоящий модуль, а не его пересказ: правило «что считать своей
 * сменой» держится на трёх разных представлениях одного и того же факта —
 * claimed_by у целиком взятой смены, claim_segments у взятой части, и смесь
 * того и другого в предпросмотре опубликованной недели.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { collectMyAuctionDayClaims } from '../src/components/resources/shiftAuctionDayClaims.js';

const DATE = '2026-06-02';
const ME = 77;
const OTHER = 999;

const lot = (over) => ({
  id: 1, shift_date: DATE, start_time: '10:00', end_time: '19:00',
  status: 'available', claimed_by: null, breaks: [], ...over,
});

test('целиком взятая смена возвращается и её можно вернуть в аукцион', () => {
  const rows = collectMyAuctionDayClaims({
    lots: [lot({ status: 'claimed', claimed_by: ME })],
    date: DATE,
    userId: ME,
  });

  assert.equal(rows.length, 1);
  assert.equal(rows[0].key, '1');
  assert.equal(rows[0].lot?.id, 1);
  assert.equal(rows[0].claimLot.start_time, '10:00');
});

test('чужая смена и свободный лот не попадают в свои смены', () => {
  const rows = collectMyAuctionDayClaims({
    lots: [
      lot({ id: 1, status: 'claimed', claimed_by: OTHER }),
      lot({ id: 2 }),
    ],
    date: DATE,
    userId: ME,
  });

  assert.deepEqual(rows, []);
});

test('взятая в доборе часть видна, хотя лот остался свободным', () => {
  // Именно этот случай раньше показывал в баре «Пусто»: частичный добор не
  // трогает lot.claimed_by, доля оператора живёт только в claim_segments.
  const rows = collectMyAuctionDayClaims({
    lots: [lot({
      status: 'available',
      claimed_by: null,
      claim_segments: [
        { claimed_by: OTHER, start_time: '10:00', end_time: '15:00' },
        { claimed_by: ME, start_time: '15:00', end_time: '19:00' },
      ],
    })],
    date: DATE,
    userId: ME,
  });

  assert.equal(rows.length, 1);
  assert.equal(rows[0].claimLot.claim_start_time, '15:00');
  assert.equal(rows[0].claimLot.claim_end_time, '19:00');
  assert.equal(rows[0].claimLot.post_auction_claimed, true);
  // Часть смены вернуть нельзя — кнопки возврата у такой строки быть не должно.
  assert.equal(rows[0].lot, null);
});

test('смену, закрытую двумя операторами, видит и тот, кого нет в claimed_by', () => {
  // Предпросмотр опубликованной недели отдаёт claimed_by только первого из них.
  const rows = collectMyAuctionDayClaims({
    lots: [lot({
      status: 'claimed',
      claimed_by: OTHER,
      claim_segments: [
        { claimed_by: OTHER, start_time: '10:00', end_time: '15:00' },
        { claimed_by: ME, start_time: '15:00', end_time: '19:00' },
      ],
    })],
    date: DATE,
    userId: ME,
  });

  assert.equal(rows.length, 1);
  assert.equal(rows[0].claimLot.claim_start_time, '15:00');
  assert.equal(rows[0].lot, null);
});

test('обычная смена в предпросмотре приходит и как claimed, и как сегмент — строка одна', () => {
  const rows = collectMyAuctionDayClaims({
    lots: [lot({
      status: 'claimed',
      claimed_by: ME,
      claim_segments: [{ claimed_by: ME, start_time: '10:00', end_time: '19:00' }],
    })],
    date: DATE,
    userId: ME,
  });

  assert.equal(rows.length, 1);
  // Диапазон совпадает с полной сменой — значка «добор» быть не должно.
  assert.equal(rows[0].claimLot.claim_start_time, '10:00');
  assert.equal(rows[0].claimLot.claim_end_time, '19:00');
  assert.equal(rows[0].lot?.id, 1);
});

test('добор показывается, но вернуть его в аукцион нельзя', () => {
  // release_shift_auction_test_lot отвечает POST_AUCTION_LOT_NOT_RELEASABLE,
  // поэтому кнопки возврата у такой строки быть не должно.
  const rows = collectMyAuctionDayClaims({
    lots: [lot({
      status: 'claimed',
      claimed_by: ME,
      post_auction_claimed: true,
      claim_start_time: '14:00',
      claim_end_time: '19:00',
    })],
    date: DATE,
    userId: ME,
  });

  assert.equal(rows.length, 1);
  assert.equal(rows[0].lot, null);
});

test('обычную смену вернуть можно', () => {
  const rows = collectMyAuctionDayClaims({
    lots: [lot({ status: 'claimed', claimed_by: ME, post_auction_claimed: false })],
    date: DATE,
    userId: ME,
  });

  assert.equal(rows[0].lot?.id, 1);
});

test('несколько своих смен на один день — все, включая ночную', () => {
  const rows = collectMyAuctionDayClaims({
    lots: [
      lot({ id: 1, status: 'claimed', claimed_by: ME }),
      lot({ id: 2, status: 'claimed', claimed_by: ME, start_time: '20:00', end_time: '08:00' }),
      lot({ id: 3, status: 'claimed', claimed_by: OTHER }),
    ],
    date: DATE,
    userId: ME,
  });

  assert.deepEqual(rows.map((row) => row.key), ['1', '2']);
});

test('смены соседних дней в день не попадают', () => {
  const rows = collectMyAuctionDayClaims({
    lots: [
      lot({ id: 1, shift_date: '2026-06-01', status: 'claimed', claimed_by: ME }),
      lot({ id: 2, status: 'claimed', claimed_by: ME }),
    ],
    date: DATE,
    userId: ME,
  });

  assert.deepEqual(rows.map((row) => row.key), ['2']);
});

test('id сравнивается по значению — строка из API равна числу', () => {
  const rows = collectMyAuctionDayClaims({
    lots: [lot({ status: 'claimed', claimed_by: String(ME) })],
    date: DATE,
    userId: String(ME),
  });

  assert.equal(rows.length, 1);
});

test('без дня, без пользователя и без лотов возвращается пустой список', () => {
  assert.deepEqual(collectMyAuctionDayClaims({ lots: [lot()], date: '', userId: ME }), []);
  assert.deepEqual(collectMyAuctionDayClaims({ lots: [lot()], date: DATE, userId: null }), []);
  assert.deepEqual(collectMyAuctionDayClaims({ lots: null, date: DATE, userId: ME }), []);
});
