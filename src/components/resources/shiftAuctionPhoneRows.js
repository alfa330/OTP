/*
 * Что показать у смены в телефонном виде «Аукциона смен».
 *
 * На компьютере день — колонка сетки, и состояние смены читается цветом ячейки
 * и подсказкой по наведению. На телефоне сетки нет: день — это список строк, у
 * каждой строки одно действие справа («Взять», «Добрать») или его отсутствие с
 * причиной словами, потому что наведения там нет и серую ячейку с подсказкой
 * человек просто не поймёт.
 *
 * Порядок проверок — ровно порядок веток AuctionLotCell в ShiftAuctionView.jsx:
 * сначала добор после аукциона, потом свободная смена в ходе аукциона, потом
 * взятая. Разойдись они, телефон предлагал бы взять смену, которую сетка на
 * компьютере показывает серой, и наоборот. Причину отказа модуль не выдумывает —
 * её считает раздел (claimBlockReasonByLotId), здесь только выбор ветки.
 */

export const AUCTION_PHONE_ROW_KIND = Object.freeze({
  TAKE: 'take',       // свободна, взять можно прямо сейчас
  TOPUP: 'topup',     // свободна после аукциона — берётся добором
  BLOCKED: 'blocked', // свободна, но правило не пускает — причина рядом
  CLOSED: 'closed',   // свободна, но выбор сейчас не идёт: до старта, пауза, итоги
  MINE: 'mine',       // взята мной
  TAKEN: 'taken',     // взята коллегой
  FREE: 'free',       // свободна — так её видит руководитель, брать ему нечего
});

const KIND = AUCTION_PHONE_ROW_KIND;

// Та же формулировка, что у раздела для этого случая: подпись не должна
// меняться от того, какой из двух путей её посчитал.
const NO_FREE_INTERVAL = 'Нет свободного интервала без пересечения';

export const classifyAuctionLotForPhone = ({
  lot,
  userId,
  canManage = false,
  canClaim = false,
  blockReason = '',
  postAuctionActive = false,
  postAuctionOption = null,
  hasStarted = false,
} = {}) => {
  if (!lot) return null;
  const isFree = lot.status === 'available' || lot.status === 'cancelled';
  const reason = String(blockReason || '');

  // Добор: смена ещё свободна, не началась и не взята добором раньше.
  if (postAuctionActive && !canManage && isFree && !lot.post_auction_claimed && !hasStarted) {
    if (reason) return { kind: KIND.BLOCKED, reason };
    if (!postAuctionOption || postAuctionOption.canClaim) return { kind: KIND.TOPUP, reason: '' };
    return { kind: KIND.BLOCKED, reason: NO_FREE_INTERVAL };
  }

  if (lot.status === 'claimed') {
    const myId = Number(userId);
    const mine = Number.isFinite(myId) && lot.claimed_by != null && Number(lot.claimed_by) === myId;
    return { kind: mine ? KIND.MINE : KIND.TAKEN, reason: '' };
  }

  if (canManage) return { kind: KIND.FREE, reason: '' };

  if (lot.status === 'available') {
    if (reason) return { kind: KIND.BLOCKED, reason };
    return { kind: canClaim ? KIND.TAKE : KIND.CLOSED, reason: '' };
  }

  return { kind: KIND.CLOSED, reason: '' };
};

/*
 * Подпись под числом в полосе дней. Цвет — только у состояний, ради которых
 * человек и смотрит на полосу: своя смена, выходной, закрытый статусом день.
 * Обычный день подписан числом свободных смен без цвета.
 */
export const describeAuctionPhoneDay = (item, { canMonitor = false, shiftLabel = '' } = {}) => {
  if (!item) return { caption: '', tone: 'none' };
  if (canMonitor) {
    const total = Number(item.total || 0);
    const claimed = Number(item.claimed || 0);
    if (!total) return { caption: '', tone: 'none' };
    return { caption: `${claimed}/${total}`, tone: claimed >= total ? 'done' : 'none' };
  }
  if (item.state === 'blocked') return { caption: item.blockedLabel || 'закрыт', tone: 'blocked' };
  if (item.state === 'off') return { caption: 'вых.', tone: 'off' };
  if (item.state === 'shift') return { caption: shiftLabel, tone: 'shift' };
  const available = Number(item.available || 0);
  if (available > 0) return { caption: `${available} св.`, tone: 'none' };
  return { caption: '—', tone: 'none' };
};
