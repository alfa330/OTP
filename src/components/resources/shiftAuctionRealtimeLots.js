/*
 * Патч одного лота аукциона по событию реального времени.
 *
 * Вынесено из ShiftAuctionView.jsx отдельным модулем по той же причине, что и
 * отбор своих смен (shiftAuctionDayClaims.js): правило неочевидное, а цена
 * ошибки — разъехавшаяся картина у разных операторов.
 *
 * Суть: событие несёт ОДИН взятый или возвращённый кусок, а список кусков
 * (lot.claim_segments) приходит только в полном снапшоте. Снапшот же грузится
 * лишь тем, кого событие касается, поэтому остальным список надо править здесь:
 *
 *  - взяли часть (lot_claimed, partial) — кусок добавить, иначе соседи видят
 *    смену полностью свободной и упрутся в SHIFT_OVERLAPS_EXISTING;
 *  - взяли добор (lot_post_auction_claimed) — то же самое, но стадия другая;
 *  - вернули часть (lot_released, partial) — кусок убрать, иначе возвращённое
 *    время так и останется занятым у всех, кроме вернувшего.
 *
 * Стадия обязана быть проставлена: shiftAuctionDayClaims считает сегмент без
 * стадии добором, а добор возвращать нельзя — свою же часть было бы не снять.
 */

const AUCTION_STAGE = 'auction';
const POST_AUCTION_STAGE = 'post_auction';

const clockValue = (value) => String(value || '').slice(0, 5);

// Кусок из события: у взятия автор лежит в claimed_by, у возврата лот уже
// обезличен (claimed_by = null), и автора несёт payload.operator_id.
const buildEventClaimSegment = (incomingLot, payload, eventType) => {
  const startTime = incomingLot?.claim_start_time || incomingLot?.claimed_start_time;
  const endTime = incomingLot?.claim_end_time || incomingLot?.claimed_end_time;
  const claimedBy = payload?.operator_id ?? incomingLot?.claimed_by;
  if (!startTime || !endTime || claimedBy == null) return null;
  return {
    claimed_by: Number(claimedBy),
    claimed_by_name: payload?.operator_name || incomingLot?.claimed_by_name || '',
    start_time: clockValue(startTime),
    end_time: clockValue(endTime),
    stage: eventType === 'lot_post_auction_claimed' ? POST_AUCTION_STAGE : AUCTION_STAGE
  };
};

const isSameClaimSegment = (item, segment) => (
  Number(item?.claimed_by) === segment.claimed_by
  && clockValue(item?.start_time) === segment.start_time
  && clockValue(item?.end_time) === segment.end_time
);

export const mergeRealtimeAuctionLot = (currentLot, incomingLot, eventType, payload) => {
  const merged = { ...currentLot, ...incomingLot, _optimistic: false };

  const touchesSegments = eventType === 'lot_post_auction_claimed'
    || ((eventType === 'lot_claimed' || eventType === 'lot_released') && Boolean(incomingLot?.partial));
  if (!touchesSegments) return merged;

  const segment = buildEventClaimSegment(incomingLot, payload, eventType);
  if (!segment) return merged;

  const existingSegments = Array.isArray(currentLot?.claim_segments) ? currentLot.claim_segments : [];
  if (eventType === 'lot_released') {
    merged.claim_segments = existingSegments.filter((item) => !isSameClaimSegment(item, segment));
    return merged;
  }

  merged.claim_segments = existingSegments.some((item) => isSameClaimSegment(item, segment))
    ? existingSegments
    : [...existingSegments, segment];
  return merged;
};

export default mergeRealtimeAuctionLot;
