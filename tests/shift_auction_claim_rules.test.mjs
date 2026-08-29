/*
 * «Не перекрывает уже взятую смену» — фронтовой близнец серверного правила.
 *
 * Правило владельца 29.08.2026 для чата: в один день можно взять ЕЩЁ часы, если
 * они не накладываются на уже взятое. Значит вместо «одна смена в день» нужна
 * точная проверка пересечения — и в ней две ловушки, обе проверяем здесь:
 * взятая ЧАСТЬ смены занимает только свои границы, а ночная смена кончается в
 * следующем дне, поэтому сравнивать внутри суток нельзя.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { findAuctionClaimConflict, auctionClaimAbsoluteRange } from '../src/components/resources/shiftAuctionClaimRules.js';

const D1 = '2026-09-01';
const D2 = '2026-09-02';

test('смены встык не пересекаются', () => {
  const claims = [{ shift_date: D1, start_time: '09:00', end_time: '15:00' }];
  assert.equal(findAuctionClaimConflict(claims, { shift_date: D1, start_time: '15:00', end_time: '21:00' }), null);
});

test('накладывающаяся смена того же дня — конфликт', () => {
  const claims = [{ shift_date: D1, start_time: '09:00', end_time: '15:00' }];
  const conflict = findAuctionClaimConflict(claims, { shift_date: D1, start_time: '12:00', end_time: '18:00' });
  assert.equal(conflict?.start_time, '09:00');
});

test('соседний день без ночей не мешает', () => {
  const claims = [{ shift_date: D1, start_time: '09:00', end_time: '18:00' }];
  assert.equal(findAuctionClaimConflict(claims, { shift_date: D2, start_time: '09:00', end_time: '18:00' }), null);
});

test('хвост ночи занимает утро следующего дня', () => {
  // 20:00–08:00 первого сентября кончается в 08:00 второго. Утренняя смена
  // второго — другая `shift_date`, и посуточное сравнение её бы пропустило.
  const claims = [{ shift_date: D1, start_time: '20:00', end_time: '08:00' }];
  const conflict = findAuctionClaimConflict(claims, { shift_date: D2, start_time: '06:00', end_time: '14:00' });
  assert.equal(conflict?.shift_date, D1);
  // А смена, начинающаяся после 08:00, свободна.
  assert.equal(findAuctionClaimConflict(claims, { shift_date: D2, start_time: '09:00', end_time: '18:00' }), null);
});

test('ночь не конфликтует с дневной сменой своего же дня, если та кончилась', () => {
  const claims = [{ shift_date: D1, start_time: '09:00', end_time: '15:00' }];
  assert.equal(findAuctionClaimConflict(claims, { shift_date: D1, start_time: '20:00', end_time: '08:00' }), null);
});

test('взятая ЧАСТЬ занимает только свои границы', () => {
  // Вызывающий передаёт фактические границы: 09:00–15:00 из смены 09:00–21:00.
  // Если бы сюда попадало окно лота, вечер того же дня был бы недоступен.
  const claims = [{ shift_date: D1, start_time: '09:00', end_time: '15:00' }];
  assert.equal(findAuctionClaimConflict(claims, { shift_date: D1, start_time: '15:00', end_time: '21:00' }), null);
});

test('битые и пустые данные конфликтом не считаются', () => {
  assert.equal(findAuctionClaimConflict(null, { shift_date: D1, start_time: '09:00', end_time: '15:00' }), null);
  assert.equal(findAuctionClaimConflict([{ shift_date: '', start_time: '', end_time: '' }],
    { shift_date: D1, start_time: '09:00', end_time: '15:00' }), null);
  assert.equal(findAuctionClaimConflict([{ shift_date: D1, start_time: '09:00', end_time: '15:00' }],
    { shift_date: D1, start_time: 'кривое', end_time: '15:00' }), null);
});

test('отрезок ночи длиннее суток не сворачивается', () => {
  const [start, end] = auctionClaimAbsoluteRange({ shift_date: D1, start_time: '20:00', end_time: '08:00' });
  assert.equal(end - start, 12 * 60);
});
