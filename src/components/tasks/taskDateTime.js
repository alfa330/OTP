/*
 * Срок «дата + время» в той же строке, что отдавало системное поле datetime-local:
 * «ГГГГ-ММ-ДДTЧЧ:ММ», местное время. Пикеры сайта отдают дату и время отдельно,
 * а всё вокруг (перевод в ISO при сохранении, черновик заметки) живёт этой
 * строкой — поэтому на стыке только разбор и сборка, без смены формата.
 *
 * Модуль отдельный, чтобы гонять его тестами через node --test.
 */

// Срок без времени встаёт на конец рабочего дня — как у быстрых кнопок и у CLI.
export const DEFAULT_DUE_TIME = '18:00';
/* «Сегодня», нажатое после конца рабочего дня, — это «до конца дня»: 18:00 уже
   прошло, а срок в прошлом задача получила бы просроченной с первой минуты. */
export const LATE_DUE_TIME = '23:59';

const LOCAL_DATE_TIME = /^(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?/;

/** «2026-11-05T14:30» → { date: '2026-11-05', time: '14:30' }; пусто — пустые строки. */
export const splitLocalDateTime = (value) => {
  const match = LOCAL_DATE_TIME.exec(String(value || ''));
  return match ? { date: match[1], time: match[2] || '' } : { date: '', time: '' };
};

/** Обратно в строку поля. Без даты срока нет вовсе — '' (как очищенное системное поле). */
export const joinLocalDateTime = (date, time) => (date ? `${date}T${time || DEFAULT_DUE_TIME}` : '');

const pad2 = (n) => String(n).padStart(2, '0');

/** День по местным часам в виде «ГГГГ-ММ-ДД» — тот же формат, что у календаря сайта. */
export const localDayIso = (date) => `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;

/** День через `days` от сегодняшнего — по календарю, а не «плюс 24 часа». */
export const dayAfter = (days, now = new Date()) => localDayIso(
  new Date(now.getFullYear(), now.getMonth(), now.getDate() + (Number(days) || 0))
);

/** Строка поля → момент по местным часам; без даты или с мусором — null. */
export const parseLocalDateTime = (value) => {
  const { date, time } = splitLocalDateTime(value);
  if (!date) return null;
  const [year, month, day] = date.split('-').map(Number);
  const [hours, minutes] = (time || DEFAULT_DUE_TIME).split(':').map(Number);
  const moment = new Date(year, month - 1, day, hours, minutes, 0, 0);
  // «2026-02-31» Date молча превратил бы в 3 марта — такую строку за срок не считаем.
  if (!Number.isFinite(moment.getTime()) || localDayIso(moment) !== date) return null;
  return moment;
};

/* Ближе минуты срок не ставится: сервер хранит отступ срока от постановки целыми
   минутами с округлением вниз, и «через 30 секунд» стало бы отступом 0 — у
   повторяющейся задачи повторы остались бы вовсе без срока. */
const MIN_LEAD_MS = 60 * 1000;

/** Срок уже наступил (или наступит в ближайшую минуту): задача была бы просрочена сразу. */
export const isLocalDateTimePast = (value, now = new Date()) => {
  const moment = parseLocalDateTime(value);
  return Boolean(moment) && moment.getTime() - now.getTime() < MIN_LEAD_MS;
};

/**
 * Быстрый срок «Сегодня / Завтра / 3 дня / Неделя»: меняется день, время остаётся
 * выбранным (как в календаре того же поля); пока времени нет — конец рабочего дня.
 * В прошлом оказаться может только «Сегодня»: тогда срок — конец рабочего дня,
 * а если и он прошёл — конец дня.
 */
export const deadlinePresetValue = (days, currentValue = '', now = new Date()) => {
  const day = dayAfter(days, now);
  const { time } = splitLocalDateTime(currentValue);
  const candidates = [time || DEFAULT_DUE_TIME, DEFAULT_DUE_TIME, LATE_DUE_TIME];
  const future = candidates
    .map((candidate) => joinLocalDateTime(day, candidate))
    .find((candidate) => !isLocalDateTimePast(candidate, now));
  return future || joinLocalDateTime(day, LATE_DUE_TIME);
};

/** Подсвечивать ли быструю кнопку: срок стоит на её день. */
export const deadlinePresetActive = (days, value, now = new Date()) => (
  Boolean(value) && splitLocalDateTime(value).date === dayAfter(days, now)
);

const capitalize = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : '');

/**
 * Срок на чипе «Дедлайн» формы: «Сегодня, 18:00», «Завтра, 10:30»,
 * «Пт, 27 нояб., 18:00» — день недели вместо счёта дней на пальцах.
 */
export const deadlineSummary = (value, now = new Date()) => {
  const moment = parseLocalDateTime(value);
  if (!moment) return '';
  const time = splitLocalDateTime(value).time || DEFAULT_DUE_TIME;
  const day = localDayIso(moment);
  if (day === dayAfter(0, now)) return `Сегодня, ${time}`;
  if (day === dayAfter(1, now)) return `Завтра, ${time}`;
  const label = new Intl.DateTimeFormat('ru-RU', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    ...(moment.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  }).format(moment);
  return `${capitalize(label)}, ${time}`;
};

/* ── Повторяющаяся задача: срок — смещение от начала повтора ──
   Сервер каждому повтору ставит срок «начало повтора + deadline_duration_minutes».
   Считаем по стенным часам (через UTC-компоненты), как и сервер: переход на
   летнее время в поясе браузера не должен сдвигать смещение на час. */
const wallClockMs = (value) => {
  const { date, time } = splitLocalDateTime(value);
  if (!date || !parseLocalDateTime(value)) return null;
  const [year, month, day] = date.split('-').map(Number);
  const [hours, minutes] = (time || DEFAULT_DUE_TIME).split(':').map(Number);
  return Date.UTC(year, month - 1, day, hours, minutes);
};

/** Строка поля, сдвинутая на `minutes` по стенным часам; без даты — ''. */
export const shiftLocalDateTime = (value, minutes) => {
  const base = wallClockMs(value);
  if (base === null) return '';
  const moved = new Date(base + Math.round(Number(minutes) || 0) * 60000);
  return `${moved.getUTCFullYear()}-${pad2(moved.getUTCMonth() + 1)}-${pad2(moved.getUTCDate())}`
    + `T${pad2(moved.getUTCHours())}:${pad2(moved.getUTCMinutes())}`;
};

/** Минуты от `from` до `to` по стенным часам; чего-то нет — null. */
export const minutesBetweenLocal = (from, to) => {
  const start = wallClockMs(from);
  const end = wallClockMs(to);
  return start === null || end === null ? null : Math.round((end - start) / 60000);
};

/** Смещение в минутах → поля «через N» сервера (часы до 23, минуты до 59). */
export const deadlineDurationFields = (totalMinutes) => {
  const total = Math.max(0, Math.round(Number(totalMinutes) || 0));
  return {
    deadline_days: String(Math.floor(total / 1440)),
    deadline_hours: String(Math.floor((total % 1440) / 60)),
    deadline_minutes: String(total % 60),
  };
};
