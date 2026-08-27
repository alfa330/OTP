/*
 * Отбор собственных смен оператора на конкретный день аукциона.
 *
 * Вынесено из ShiftAuctionView.jsx отдельным модулем, потому что правило «что
 * считать своей сменой» неочевидное и его надо покрывать тестами:
 *
 *  - целиком взятая смена приходит как lot.status === 'claimed' + claimed_by;
 *  - взятая в доборе ЧАСТЬ смены не меняет lot.claimed_by вовсе — лот остаётся
 *    'available' с пустым claimed_by, а доля оператора живёт только внутри
 *    lot.claim_segments (database.py: частичный добор пересобирает остаток);
 *  - в предпросмотре опубликованной недели смену, закрытую двумя операторами,
 *    отдают с claimed_by только первого из них, зато сегменты полны.
 *
 * Поэтому сегменты имеют приоритет над claimed_by: они точнее.
 *
 * Форматирование времени и часов остаётся в компоненте — здесь только отбор.
 */

// Синтетический лот для сегмента: помощники форматирования смотрят на
// post_auction_claimed + claim_start_time/claim_end_time и посчитают по нему
// фактический диапазон вместе с попавшими в него перерывами.
const buildSegmentClaimLot = (lot, segment) => ({
  ...lot,
  post_auction_claimed: true,
  claim_start_time: segment.start_time,
  claim_end_time: segment.end_time
});

// Добор в аукцион не возвращают: release_shift_auction_test_lot отвечает
// POST_AUCTION_LOT_NOT_RELEASABLE. Отменить его можно только в «Моих доп.
// сменах» и только первые 10 минут — предлагать здесь «Вернуть» нельзя.
const isReleasableByOperator = (lot, myId) => (
  lot.status === 'claimed'
  && Number(lot.claimed_by) === myId
  && !lot.post_auction_claimed
);

/**
 * @param {object} params
 * @param {Array} params.lots      все лоты недели (monitoredLots)
 * @param {string} params.date     дата дня, YYYY-MM-DD
 * @param {number|string} params.userId  id текущего оператора
 * @returns {Array<{key: string, claimLot: object, lot: object|null}>}
 *   claimLot — что показывать и по чему считать часы;
 *   lot — настоящий лот, если смену можно вернуть в аукцион, иначе null.
 */
export const collectMyAuctionDayClaims = ({ lots, date, userId }) => {
  const myId = Number(userId);
  if (!date || !Number.isFinite(myId) || !myId) return [];

  const rows = [];
  (Array.isArray(lots) ? lots : []).forEach((lot) => {
    if (!lot || lot.shift_date !== date) return;

    const mySegments = (Array.isArray(lot.claim_segments) ? lot.claim_segments : [])
      .filter((segment) => segment && Number(segment.claimed_by) === myId);

    if (mySegments.length) {
      const releasable = isReleasableByOperator(lot, myId);
      mySegments.forEach((segment, index) => {
        rows.push({
          key: `${lot.id}-cs${index}`,
          claimLot: buildSegmentClaimLot(lot, segment),
          // Вернуть можно только смену целиком: возврат части аукцион не умеет.
          lot: releasable && mySegments.length === 1 ? lot : null
        });
      });
      return;
    }

    if (lot.status !== 'claimed' || Number(lot.claimed_by) !== myId) return;
    rows.push({ key: String(lot.id), claimLot: lot, lot: isReleasableByOperator(lot, myId) ? lot : null });
  });

  return rows;
};

export default collectMyAuctionDayClaims;
