/*
 * «Смена не должна перекрывать уже взятую» — правило, общее с сервером.
 *
 * Вынесено отдельным модулем, потому что в нём две неочевидные вещи, и обе уже
 * ломались:
 *
 *  1. У ВЗЯТОЙ ЧАСТИ смены окно лота шире взятого. Считать по `start_time`
 *     /`end_time` лота нельзя: человек, взявший 09:00–15:00 из 09:00–21:00,
 *     выглядел бы занятым все двенадцать часов, и добрать вечер в тот же день
 *     ему бы не дали. Сюда передаются УЖЕ фактические границы.
 *  2. Сравнение внутри суток пропускает ночь. Смена 20:00–08:00 кончается в
 *     СЛЕДУЮЩЕМ дне, у него другой `shift_date`, и утренняя смена этого дня
 *     наложилась бы на её хвост незамеченной. Поэтому время сквозное:
 *     номер дня × 1440 + минуты.
 *
 * Серверный близнец — `Database._shift_auction_claim_conflict`.
 */

const MINUTES_IN_DAY = 1440;

const parseClock = (value) => {
  const [hours, minutes] = String(value || '').slice(0, 5).split(':');
  const h = Number(hours);
  const m = Number(minutes);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
  return h * 60 + m;
};

const dayNumber = (value) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
  if (!match) return null;
  const time = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isFinite(time) ? Math.round(time / 86400000) : null;
};

/** Сквозной отрезок [начало, конец) в минутах, или null. */
export const auctionClaimAbsoluteRange = (item) => {
  const day = dayNumber(item?.shift_date);
  const start = parseClock(item?.start_time);
  let end = parseClock(item?.end_time);
  if (day === null || start === null || end === null) return null;
  // Конец не позже начала — смена перешла через полночь.
  if (end <= start) end += MINUTES_IN_DAY;
  const base = day * MINUTES_IN_DAY;
  return [base + start, base + end];
};

/**
 * Первая из `claims`, что перекрывается с `candidate`, иначе null.
 * Оба вида — объекты {shift_date, start_time, end_time} с ФАКТИЧЕСКИМИ границами.
 */
export const findAuctionClaimConflict = (claims, candidate) => {
  const candidateRange = auctionClaimAbsoluteRange(candidate);
  if (!candidateRange) return null;
  return (Array.isArray(claims) ? claims : []).find((claim) => {
    const range = auctionClaimAbsoluteRange(claim);
    if (!range) return false;
    return candidateRange[0] < range[1] && range[0] < candidateRange[1];
  }) || null;
};

export default findAuctionClaimConflict;
