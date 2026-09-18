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
  MINE: 'mine',       // взята мной целиком
  MINE_PART: 'mine_part', // в смене мой кусок, взятый в ходе аукциона
  TAKEN: 'taken',     // взята коллегой
  FREE: 'free',       // свободна — так её видит руководитель, брать ему нечего
});

const KIND = AUCTION_PHONE_ROW_KIND;

/*
 * Взятая В ХОДЕ АУКЦИОНА часть смены (так разбирает смены чат) НЕ закрывает лот:
 * он остаётся `available` с пустым `claimed_by`, иначе оставшийся кусок пропал бы
 * у остальных. Значит по статусу такую смену своей не признать — только по своей
 * строке в `lot.claim_segments`. Без этой проверки сетка звала оператора «взять»
 * смену, которую он уже держит, а вернуть свой кусок из недели было нечем.
 *
 * Стадия обязательна: `post_auction` — это добор, он уже в графике и возвращается
 * не здесь, а в «Моих доп. сменах» (см. shiftAuctionDayClaims).
 */
const holdsAuctionClaimPart = (lot, userId) => {
  const myId = Number(userId);
  if (!Number.isFinite(myId) || !myId) return false;
  return (Array.isArray(lot?.claim_segments) ? lot.claim_segments : []).some((segment) => (
    Number(segment?.claimed_by) === myId
    && String(segment?.stage || 'post_auction') === 'auction'
  ));
};

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
    if (mine) return { kind: KIND.MINE, reason: '' };
    // Смену закрыли несколько человек: `claimed_by` достался последнему, а мой
    // кусок в ней всё равно мой — иначе она выглядела бы чужой.
    if (holdsAuctionClaimPart(lot, userId)) return { kind: KIND.MINE_PART, reason: '' };
    return { kind: KIND.TAKEN, reason: '' };
  }

  if (canManage) return { kind: KIND.FREE, reason: '' };

  // Свой кусок старше причины отказа: она про то, чтобы взять ЕЩЁ, а вернуть уже
  // взятое человек вправе в любом случае.
  if (holdsAuctionClaimPart(lot, userId)) return { kind: KIND.MINE_PART, reason: '' };

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

const DAY_MINUTES = 1440;

// Номер дня от эпохи: разница дат без часовых поясов и перевода часов.
const toDayNumber = (value) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
  if (!match) return null;
  return Math.round(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) / 86400000);
};

/*
 * Лента дня 00–24 над своими сменами дня. Минуты смены и перерывов считаются от
 * полуночи ДАТЫ СМЕНЫ и у ночи уходят за 1440 (20:00–08:00 — это 1200–1920),
 * поэтому смена кладётся на ленту со сдвигом на разницу дат и обрезается по
 * суткам: вечер ночи — на ленте её дня, хвост до 08:00 — на ленте следующего.
 * Перерывы — доли внутри своей полосы, как у ленты «Моих смен».
 */
export const buildAuctionPhoneDayTimelineParts = ({ date, entries = [] } = {}) => {
  const day = toDayNumber(date);
  if (day === null) return [];
  const parts = [];
  (Array.isArray(entries) ? entries : []).forEach((entry) => {
    const entryDay = toDayNumber(entry?.date);
    const start = Number(entry?.start);
    const end = Number(entry?.end);
    if (entryDay === null || !Number.isFinite(start) || !Number.isFinite(end) || end <= start) return;
    const offset = (entryDay - day) * DAY_MINUTES;
    const from = Math.max(0, start + offset);
    const to = Math.min(DAY_MINUTES, end + offset);
    if (to - from < 1) return;
    const span = to - from;
    const breaks = (Array.isArray(entry.breaks) ? entry.breaks : [])
      .map((item) => {
        const breakStart = Number(item?.start);
        let breakEnd = Number(item?.end);
        if (!Number.isFinite(breakStart) || !Number.isFinite(breakEnd)) return null;
        if (breakEnd <= breakStart) breakEnd += DAY_MINUTES;
        const left = Math.max(from, breakStart + offset);
        const right = Math.min(to, breakEnd + offset);
        return right > left ? { left: ((left - from) / span) * 100, width: ((right - left) / span) * 100 } : null;
      })
      .filter(Boolean);
    parts.push({
      key: `${entry.key}@${date}`,
      startMin: from,
      endMin: to,
      left: (from / DAY_MINUTES) * 100,
      width: (span / DAY_MINUTES) * 100,
      breaks,
    });
  });
  return parts.sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin);
};

/*
 * Строки сетки одной ставки: i-я строка — i-я по времени смена каждого дня, как
 * в сетке на сайте. День, где смен меньше, чем строк, получает пустую клетку.
 */
export const buildAuctionPhoneGridRows = (lotsByDate, dates = []) => {
  const byDate = lotsByDate instanceof Map ? lotsByDate : new Map();
  const list = Array.isArray(dates) ? dates : [];
  const rowCount = list.reduce((max, date) => Math.max(max, (byDate.get(date) || []).length), 0);
  return Array.from({ length: rowCount }, (_, index) => list.map((date) => (byDate.get(date) || [])[index] || null));
};

export const AUCTION_PHONE_CELL_ACTION = Object.freeze({
  DETAILS: 'details', // руководитель: кто какую часть смены взял
  CONFIRM: 'confirm', // лист снизу со сменой и «Взять»
  PARTIAL: 'partial', // чат: сразу экран выбора части с таймлайном
  TOPUP: 'topup',     // добор: сразу экран выбора интервала
  RELEASE: 'release', // своя смена: лист «Вернуть смену»
  PART: 'part',       // свой кусок смены: лист «Вернуть часть» и «Взять ещё»
  DAY: 'day',         // своя смена, которую не вернуть: экран дня
  INFO: 'info',       // взять нельзя: лист с причиной
});

/*
 * Что делает нажатие на ячейку. На сайте нажатие на свободную смену линии сразу
 * её берёт; на телефоне ячейка шириной в палец, и промахом легко взять соседнюю,
 * поэтому сначала лист со сменой и кнопкой «Взять». Чат и добор и на сайте
 * открывают экран выбора интервала — он сам и есть подтверждение, второй шаг
 * перед ним был бы лишним.
 */
export const pickAuctionPhoneCellAction = ({ kind, canManage = false, supportsPartialClaim = false, releasable = false } = {}) => {
  const ACTION = AUCTION_PHONE_CELL_ACTION;
  if (canManage) return ACTION.DETAILS;
  if (kind === KIND.TAKE) return supportsPartialClaim ? ACTION.PARTIAL : ACTION.CONFIRM;
  if (kind === KIND.TOPUP) return ACTION.TOPUP;
  if (kind === KIND.MINE) return releasable ? ACTION.RELEASE : ACTION.DAY;
  // У куска действий два — вернуть свой и добрать оставшееся, — поэтому лист, а
  // не сразу возврат: одним нажатием отдавать смену, придя за добором, нельзя.
  if (kind === KIND.MINE_PART) return releasable ? ACTION.PART : ACTION.DAY;
  return ACTION.INFO;
};
