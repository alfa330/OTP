/*
 * Отбор собственных смен оператора на конкретный день аукциона.
 *
 * Вынесено из ShiftAuctionView.jsx отдельным модулем, потому что правило «что
 * считать своей сменой» неочевидное и его надо покрывать тестами:
 *
 *  - целиком взятая смена приходит как lot.status === 'claimed' + claimed_by;
 *  - взятая ЧАСТЬ смены не меняет lot.claimed_by вовсе — лот остаётся
 *    'available' с пустым claimed_by, а доля оператора живёт только внутри
 *    lot.claim_segments (database.py: частичный добор пересобирает остаток);
 *  - часть бывает двух стадий: 'auction' — взята прямо в ходе аукциона (так
 *    разбирает смены чат, и её МОЖНО вернуть кнопкой «Вернуть») и
 *    'post_auction' — добор после публикации, он уже в графике и возвращается
 *    только через «Мои доп. смены» в течение 10 минут;
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
const isAuctionStageSegment = (segment) => String(segment?.stage || 'post_auction') === 'auction';

const buildSegmentClaimLot = (lot, segment) => ({
  ...lot,
  // Пост-аукционный добор помечаем прежним флагом (на него смотрит вся разметка),
  // а часть, взятую в ходе аукциона, — своим: она НЕ добор и рисуется без бейджа.
  post_auction_claimed: !isAuctionStageSegment(segment),
  partial_claim: true,
  claim_stage: isAuctionStageSegment(segment) ? 'auction' : 'post_auction',
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
        // Часть, взятую в ходе аукциона, вернуть МОЖНО: release снимает именно её
        // строку и не трогает куски других операторов. Для пост-аукционного добора
        // возврат по-прежнему закрыт — смена уже сохранена в график.
        const auctionStage = isAuctionStageSegment(segment);
        rows.push({
          key: `${lot.id}-cs${index}`,
          claimLot: buildSegmentClaimLot(lot, segment),
          lot: auctionStage
            ? lot
            : (releasable && mySegments.length === 1 ? lot : null)
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
