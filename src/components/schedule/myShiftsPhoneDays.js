/*
 * «Мои смены» на телефоне — решения о том, ЧТО показывать в полосе дней и в
 * списке коллег. Здесь нет React и нет данных портала: только чистые функции,
 * которые можно прогнать node-тестом (tests/my_shifts_phone_days.test.mjs).
 *
 * Разметка — MyShiftsMobile.jsx, ветка isNarrowShell в App.jsx
 * (ShiftPlannerViewWithCalendar).
 */

const toMinutes = (value) => {
  if (typeof value !== 'string' || !/^\d{1,2}:\d{2}$/.test(value)) return NaN;
  const [h, m] = value.split(':').map(Number);
  return h * 60 + m;
};

/* Время без ведущего нуля и без «:00»: «9–18», «9:30–18», у ночной «20–8».
   В полосе дней на день приходится седьмая часть экрана, «09:00–18:00» туда
   не помещается. */
export const compactClock = (value) => {
  const minutes = toMinutes(value);
  if (!Number.isFinite(minutes)) return String(value || '');
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  return m ? `${h}:${String(m).padStart(2, '0')}` : String(h);
};

export const shortShiftCaption = (seg) => {
  if (!seg) return '';
  return `${compactClock(seg.start)}–${compactClock(seg.end)}`;
};

/*
 * Подпись и тон дня в полосе. Смена важнее статуса, статус важнее выходного:
 * в день с отпуском смен не бывает, а «Вых.» рядом с «отпуск» — две подписи
 * об одном. Две и больше смен подписываем числом: «9–13 12–18» не читается.
 */
export const describeMyShiftsPhoneDay = (dayCard) => {
  if (!dayCard) return { caption: '', tone: 'none' };
  const shifts = Array.isArray(dayCard.shifts) ? dayCard.shifts : [];
  if (shifts.length > 1) return { caption: `${shifts.length} смены`, tone: 'shift' };
  if (shifts.length === 1) return { caption: shortShiftCaption(shifts[0]), tone: 'shift' };
  const status = dayCard.scheduleStatus;
  if (status && (status.shortLabel || status.label)) {
    return { caption: String(status.shortLabel || status.label), tone: 'blocked' };
  }
  if (dayCard.isDayOff) return { caption: 'Вых.', tone: 'off' };
  return { caption: '—', tone: 'none' };
};

/* Выбранный день обязан лежать в видимой неделе: неделю листают стрелками, и
   выбор с прошлой недели там ничему не соответствует. Сегодня — если оно в
   этой неделе, иначе первый день. */
export const pickPhoneDayDate = (selected, range, today) => {
  const days = Array.isArray(range) ? range : [];
  if (!days.length) return selected || today || '';
  if (days.includes(selected)) return selected;
  if (days.includes(today)) return today;
  return days[0];
};

const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

const parseIso = (value) => {
  const [y, m, d] = String(value || '').split('-').map(Number);
  if (!y || !m || !d) return null;
  return { y, m, d };
};

/* «8 — 14 сентября», на стыке месяцев — «28 сентября — 4 октября». */
export const formatPhoneWeekLabel = (range) => {
  const days = Array.isArray(range) ? range : [];
  const first = parseIso(days[0]);
  const last = parseIso(days[days.length - 1]);
  if (!first) return '';
  if (!last || (first.m === last.m && first.y === last.y)) {
    if (!last || first.d === last.d) return `${first.d} ${MONTHS_GENITIVE[first.m - 1]}`;
    return `${first.d} — ${last.d} ${MONTHS_GENITIVE[first.m - 1]}`;
  }
  return `${first.d} ${MONTHS_GENITIVE[first.m - 1]} — ${last.d} ${MONTHS_GENITIVE[last.m - 1]}`;
};

/*
 * Коллеги на день: сначала те, кто на смене (по времени начала), потом
 * выходные, потом без смен. На компьютере это сетка «люди × дни», где порядок
 * строк не важен; в списке одного дня наверху должны быть те, кого искали —
 * кто сегодня работает.
 */
export const colleaguesForPhoneDay = (operators, dateStr) => {
  const list = Array.isArray(operators) ? operators : [];
  const rows = list.map((op) => {
    const shifts = Array.isArray(op?.shifts?.[dateStr]) ? op.shifts[dateStr] : [];
    const isDayOff = Array.isArray(op?.daysOff) && op.daysOff.includes(dateStr) && shifts.length === 0;
    const firstStart = shifts.length ? Math.min(...shifts.map((sh) => toMinutes(sh?.start)).filter(Number.isFinite)) : Infinity;
    return { op, shifts, isDayOff, firstStart: Number.isFinite(firstStart) ? firstStart : Infinity };
  });
  rows.sort((a, b) => {
    const rank = (row) => (row.shifts.length ? 0 : row.isDayOff ? 1 : 2);
    return (rank(a) - rank(b)) || (a.firstStart - b.firstStart) || String(a.op?.name || '').localeCompare(String(b.op?.name || ''), 'ru');
  });
  return rows;
};

export const describeColleaguesPhoneDay = (operators, dateStr) => {
  const onShift = colleaguesForPhoneDay(operators, dateStr).filter((row) => row.shifts.length).length;
  return { caption: onShift ? `${onShift} чел.` : '—', tone: onShift ? 'none' : 'none' };
};
