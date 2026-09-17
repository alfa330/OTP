/*
 * Правила телефонного вида «Расчета ресурсов» — без React.
 *
 * Разметка живёт в ResourceFteMobile.jsx, расчёты раздела — в ResourceFteView.jsx
 * и ResourceSchedulePlanner.jsx. Здесь только то, в чём легко ошибиться и что
 * нельзя проверить глазами на одном стенде: подписи периодов через границу
 * месяца и года, сетка календаря с понедельника, выбор периода двумя касаниями,
 * время смены через полночь.
 *
 * Даты — строки YYYY-MM-DD и собираются из getFullYear/getMonth/getDate:
 * toISOString в Asia/Almaty с полуночи до пяти утра отдаёт вчерашний день
 * (страж tests/test_resource_fte_chat.py запрещает его в чатовой части раздела).
 */

const WEEKDAYS_SHORT = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

const MONTHS_GENITIVE = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

const MONTHS_NOMINATIVE = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

const pad2 = (value) => String(value).padStart(2, '0');

export const parsePhoneIso = (iso) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(date.getTime()) ? null : date;
};

export const toPhoneIso = (date) => (
  `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
);

export const addPhoneDays = (iso, days) => {
  const date = parsePhoneIso(iso);
  if (!date) return '';
  date.setDate(date.getDate() + Number(days || 0));
  return toPhoneIso(date);
};

/* «21.09». Год не пишем: на телефоне период почти всегда в текущем году, а
   лишние пять знаков переносят подпись на вторую строку. */
export const formatPhoneShortDate = (iso) => {
  const date = parsePhoneIso(iso);
  return date ? `${pad2(date.getDate())}.${pad2(date.getMonth() + 1)}` : '—';
};

/* Период строкой. Год появляется, только когда концы в разных годах: иначе
   «28.12 — 03.01» читается как период назад во времени. */
export const formatPhoneRange = (startIso, endIso) => {
  const start = parsePhoneIso(startIso);
  const end = parsePhoneIso(endIso);
  if (!start && !end) return '—';
  if (!start || !end) return formatPhoneShortDate(startIso || endIso);
  if (toPhoneIso(start) === toPhoneIso(end)) return formatPhoneShortDate(startIso);
  if (start.getFullYear() !== end.getFullYear()) {
    return `${formatPhoneShortDate(startIso)}.${start.getFullYear()} — ${formatPhoneShortDate(endIso)}.${end.getFullYear()}`;
  }
  return `${formatPhoneShortDate(startIso)} — ${formatPhoneShortDate(endIso)}`;
};

/* «Пн, 21 сентября» — заголовок дня на экране и в карточке. */
export const formatPhoneDayTitle = (iso) => {
  const date = parsePhoneIso(iso);
  if (!date) return '';
  return `${WEEKDAYS_SHORT[date.getDay()]}, ${date.getDate()} ${MONTHS_GENITIVE[date.getMonth()]}`;
};

export const formatPhoneWeekday = (iso) => {
  const date = parsePhoneIso(iso);
  return date ? WEEKDAYS_SHORT[date.getDay()] : '';
};

export const formatPhoneMonthTitle = (date) => (
  date ? `${MONTHS_NOMINATIVE[date.getMonth()]} ${date.getFullYear()}` : ''
);

export const formatPhoneHour = (hour) => `${pad2(Math.max(0, Math.min(23, Number(hour) || 0)))}:00`;

/*
 * Плитки показателей — двумя колонками. Нечётная последняя растягивается на всю
 * ширину: пустая ячейка рядом с ней читается как «сюда что-то не приехало»
 * (то же решение принято у карточек биллинга чата на компьютере).
 */
export const phoneTileSpans = (count) => {
  const total = Math.max(0, Number(count) || 0);
  return Array.from({ length: total }, (_, index) => total % 2 === 1 && index === total - 1);
};

/*
 * Месяц календаря неделями с понедельника: всегда шесть строк, чтобы экран не
 * прыгал по высоте при листании. Дни соседних месяцев помечены outside.
 */
export const buildPhoneCalendarMonth = (monthDate) => {
  const base = monthDate instanceof Date ? monthDate : parsePhoneIso(monthDate);
  if (!base) return [];
  const first = new Date(base.getFullYear(), base.getMonth(), 1);
  const shift = (first.getDay() + 6) % 7;
  const cursor = new Date(first.getFullYear(), first.getMonth(), 1 - shift);
  const weeks = [];
  for (let row = 0; row < 6; row += 1) {
    const week = [];
    for (let col = 0; col < 7; col += 1) {
      week.push({
        iso: toPhoneIso(cursor),
        day: cursor.getDate(),
        outside: cursor.getMonth() !== first.getMonth(),
      });
      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push(week);
  }
  return weeks;
};

/*
 * Выбор периода двумя касаниями: первое ставит начало, второе — конец. Касание
 * раньше начала переставляет концы местами, а не сбрасывает выбор: на телефоне
 * промах на день раньше — частый случай, и терять первое касание обидно.
 */
export const nextPhoneRangeSelection = (draftStart, iso) => {
  if (!iso) return { draftStart, range: null };
  if (!draftStart) return { draftStart: iso, range: null };
  return iso < draftStart
    ? { draftStart: '', range: [iso, draftStart] }
    : { draftStart: '', range: [draftStart, iso] };
};

export const clockToPhoneMinutes = (value) => {
  const match = /^(\d{1,2}):(\d{2})/.exec(String(value || ''));
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
};

export const phoneMinutesToClock = (minutes) => {
  const total = ((Math.round(Number(minutes) || 0) % 1440) + 1440) % 1440;
  return `${pad2(Math.floor(total / 60))}:${pad2(total % 60)}`;
};

/*
 * Окно времени биллинга: границы включительно, начало не позже конца. Правило
 * то же, что у настольного пикера (TimeRangePicker.emitTime): сдвинутый край
 * тянет за собой второй, а не отменяет правку.
 */
export const orderPhoneTimeRange = (which, nextValue, startValue, endValue) => {
  const next = clockToPhoneMinutes(nextValue);
  if (next === null) return [startValue, endValue];
  const clock = phoneMinutesToClock(next);
  if (which === 'start') {
    const end = clockToPhoneMinutes(endValue);
    return [clock, end !== null && next > end ? clock : endValue];
  }
  const start = clockToPhoneMinutes(startValue);
  return [start !== null && next < start ? clock : startValue, clock];
};

export const PHONE_SHIFT_MIN_MINUTES = 60;
export const PHONE_SHIFT_MAX_END_MINUTES = 32 * 60;

/*
 * Время смены из двух полей «Начало» и «Конец». На компьютере смену тянут мышью
 * по полотну, на телефоне — задают временем. Конец раньше начала или равный ему
 * значит «на следующие сутки» (ночь 20:00–08:00). Ограничения — те же, что у
 * перетаскивания в планировщике: не короче часа и не дальше 08:00 следующего дня.
 */
export const phoneShiftEditRange = (startClock, endClock) => {
  const start = clockToPhoneMinutes(startClock);
  const endRaw = clockToPhoneMinutes(endClock);
  if (start === null || endRaw === null) {
    return { valid: false, error: 'Укажите начало и конец' };
  }
  const end = endRaw <= start ? endRaw + 1440 : endRaw;
  if (end - start < PHONE_SHIFT_MIN_MINUTES) {
    return { valid: false, error: 'Смена не может быть короче часа', startMinute: start, endMinute: end };
  }
  if (end > PHONE_SHIFT_MAX_END_MINUTES) {
    return { valid: false, error: 'Смена не может закончиться позже 08:00 следующего дня', startMinute: start, endMinute: end };
  }
  return { valid: true, error: '', startMinute: start, endMinute: end, overnight: end > 1440 };
};

export const formatPhoneDuration = (minutes) => {
  const total = Math.max(0, Math.round(Number(minutes) || 0));
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  if (!rest) return `${hours} ч`;
  if (!hours) return `${rest} мин`;
  return `${hours} ч ${rest} мин`;
};

/* Подпись вкладки на телефоне: «Биллинг Oktell» не помещается рядом с
   соседями, а слово «Oktell» на своём разделе ничего не добавляет. */
const PHONE_TAB_LABELS = { oktell_billing: 'Биллинг' };

export const phoneTabLabel = (tab) => PHONE_TAB_LABELS[tab?.key] || tab?.label || '';
