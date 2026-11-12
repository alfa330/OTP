/*
 * Подписи сверки загруженных смен (аукцион Тез КЦ, задача #124).
 *
 * Чистые функции: окно ShiftAuctionUploadSheet только раскладывает то, что
 * посчитал сервер (db/shift_auction.py: _shift_auction_upload_state_tx).
 */

const WEEKDAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

const parseIsoDate = (value) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
  if (!match) return null;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return Number.isNaN(date.getTime()) ? null : date;
};

const twoDigits = (value) => String(value).padStart(2, '0');

/** «2026-11-16» → «пн, 16.11». */
export const formatUploadDayLabel = (value) => {
  const date = parseIsoDate(value);
  if (!date) return String(value || '');
  return `${WEEKDAYS[date.getUTCDay()]}, ${twoDigits(date.getUTCDate())}.${twoDigits(date.getUTCMonth() + 1)}`;
};

const dayWord = (count) => {
  const value = Math.abs(Number(count || 0)) % 100;
  const last = value % 10;
  if (value > 10 && value < 20) return 'дней';
  if (last === 1) return 'день';
  if (last >= 2 && last <= 4) return 'дня';
  return 'дней';
};

/** 1 смена, 2 смены, 5 смен, 21 смена. */
export const formatUploadShiftCount = (count) => {
  const value = Math.abs(Number(count || 0));
  const lastTwo = value % 100;
  const last = value % 10;
  let word = 'смен';
  if (!(lastTwo > 10 && lastTwo < 20)) {
    if (last === 1) word = 'смена';
    else if (last >= 2 && last <= 4) word = 'смены';
  }
  return `${value} ${word}`;
};

/** «16.11–22.11 · 7 дней». */
export const formatUploadPeriod = (from, to) => {
  const start = parseIsoDate(from);
  const end = parseIsoDate(to);
  if (!start || !end) return '';
  const label = (date) => `${twoDigits(date.getUTCDate())}.${twoDigits(date.getUTCMonth() + 1)}`;
  const days = Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
  return `${label(start)}–${label(end)} · ${days} ${dayWord(days)}`;
};

/** Минуты → часы с запятой: 22650 → «377,5», 600 → «10». */
export const formatUploadHours = (minutes) => {
  const hours = Math.round((Number(minutes || 0) / 60) * 10) / 10;
  return String(hours).replace('.', ',');
};

const compactClock = (value) => {
  const [hours, minutes] = String(value || '').slice(0, 5).split(':');
  const h = Number(hours);
  if (!Number.isFinite(h)) return String(value || '');
  return minutes && minutes !== '00' ? `${h}:${minutes}` : String(h);
};

/** {start: '13:00', end: '19:30'} → «13–19:30»; ночь «21–9». */
export const formatUploadShift = (shift) => `${compactClock(shift?.start)}–${compactClock(shift?.end)}`;

/** ISO-время → «12.11 в 12:51». */
export const formatUploadWhen = (value) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(value || ''));
  if (!match) return '';
  return `${match[3]}.${match[2]} в ${match[4]}:${match[5]}`;
};

export const uploadDayChanged = (day) => Boolean(
  (Array.isArray(day?.added) && day.added.length) || (Array.isArray(day?.removed) && day.removed.length)
);

/**
 * Смены дня с отметкой «добавится»: поштучно, а не по совпадению времени —
 * если в дне две смены 9–13, а добавилась одна, зелёной должна стать одна.
 */
export const uploadDayShiftTones = (day) => {
  const pending = (Array.isArray(day?.added) ? day.added : []).map((item) => `${item.start}|${item.end}`);
  return (Array.isArray(day?.shifts) ? day.shifts : []).map((shift) => {
    const key = `${shift.start}|${shift.end}`;
    const index = pending.indexOf(key);
    if (index >= 0) {
      pending.splice(index, 1);
      return { shift, tone: 'added' };
    }
    return { shift, tone: 'plain' };
  });
};
