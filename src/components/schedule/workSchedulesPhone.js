/*
 * «Графики работы» на телефоне — решения о том, ЧТО показывать в списке людей,
 * в полосе дней и в строке дня. Здесь нет React и нет данных портала: только
 * чистые функции, которые можно прогнать node-тестом
 * (tests/work_schedules_phone.test.mjs).
 *
 * Разметка — WorkSchedulesMobile.jsx, ветка isNarrowShell в App.jsx
 * (ShiftPlannerViewWithCalendar, ниже ветки оператора).
 *
 * Правила подписей дословно повторяют настольную сетку: ночная смена относится
 * к дню своего НАЧАЛА в «Неделе» и «Месяце» (иначе 20:00 — 02:00 считалась бы
 * дважды) и показывается обоими днями в «Дне»; часы дня считаются по сменам
 * этого дня, а если их нет — по хвосту ночной.
 */

const toMinutes = (value) => {
  if (typeof value !== 'string' || !/^\d{1,2}:\d{2}$/.test(value)) return NaN;
  const [h, m] = value.split(':').map(Number);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return NaN;
  return h * 60 + m;
};

const shiftMinutes = (shift) => {
  const start = toMinutes(shift?.start);
  let end = toMinutes(shift?.end);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
  if (end <= start) end += 1440;
  return Math.max(0, end - start);
};

const shiftsOf = (op, dateStr) => (Array.isArray(op?.shifts?.[dateStr]) ? op.shifts[dateStr] : []);

const isCrossingShift = (shift) => {
  const start = toMinutes(shift?.start);
  const end = toMinutes(shift?.end);
  return Number.isFinite(start) && Number.isFinite(end) && end <= start && shift?.end !== '00:00';
};

const isPracticeShift = (shift) => {
  const raw = String(shift?.shift_type ?? shift?.shiftType ?? '').trim().toLowerCase();
  return raw === 'office_practice' || raw === 'practice' || raw === 'практика' || raw === 'практика в офисе';
};

const shiftDate = (dateStr, step) => {
  const [y, m, d] = String(dateStr || '').split('-').map(Number);
  if (!y || !m || !d) return '';
  const date = new Date(y, m - 1, d + step);
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};

/* Время смены строкой: «09:00 — 18:00», у ночной с пометкой «+1». */
export const plannerPhoneShiftText = (shift) => {
  if (!shift) return '';
  return `${shift.start} — ${shift.end}${isCrossingShift(shift) ? ' +1' : ''}`;
};

/* Часы по-русски: «7,5 ч», ноль — прочерк. Десятые нужны: смена 7:45 и смена
   8:00 в списке из тридцати человек различимы только ими. */
export const formatPlannerPhoneHours = (minutes) => {
  const value = Math.round(((Number(minutes) || 0) / 60) * 10) / 10;
  if (!value) return '—';
  return `${String(value).replace('.', ',')} ч`;
};

/*
 * Ячейка «человек × день» для телефона. `viewMode` решает судьбу ночной смены
 * ровно так же, как настольная сетка: в «Дне» показываем и хвост вчерашней, в
 * «Неделе» и «Месяце» — только начавшиеся в этот день.
 */
export const plannerPhoneDayCell = (op, dateStr, { viewMode = 'week', status = null } = {}) => {
  const own = shiftsOf(op, dateStr);
  const labels = own.map((shift, idx) => ({
    text: plannerPhoneShiftText(shift),
    crossing: isCrossingShift(shift),
    practice: isPracticeShift(shift),
    carry: false,
    idx,
  }));
  if (viewMode === 'day') {
    shiftsOf(op, shiftDate(dateStr, -1)).forEach((shift, idx) => {
      if (!isCrossingShift(shift)) return;
      labels.push({
        text: plannerPhoneShiftText(shift),
        crossing: true,
        practice: isPracticeShift(shift),
        carry: true,
        idx,
      });
    });
  }
  const ownMin = own.reduce((acc, shift) => acc + shiftMinutes(shift), 0);
  const carryMin = labels
    .filter((label) => label.carry)
    .reduce((acc, label) => acc + shiftMinutes(shiftsOf(op, shiftDate(dateStr, -1))[label.idx]), 0);
  const workMin = ownMin > 0 ? ownMin : carryMin;
  const isDayOff = Array.isArray(op?.daysOff) && op.daysOff.includes(dateStr) && labels.length === 0;
  return {
    date: dateStr,
    labels,
    workMin,
    hasShift: labels.length > 0,
    isDayOff,
    status: status || null,
  };
};

/*
 * Подпись ячейки в строке списка. Статус важнее смены: в день отпуска смен не
 * бывает, а «Отпуск · 09:00 — 18:00» — две подписи об одном. Две и больше смен
 * сводим к числу: два интервала в строку списка не помещаются.
 */
export const plannerPhoneCellCaption = (cell) => {
  if (!cell) return { caption: '—', tone: 'none' };
  if (cell.status && (cell.status.label || cell.status.shortLabel)) {
    return { caption: String(cell.status.shortLabel || cell.status.label), tone: 'blocked' };
  }
  if (cell.labels.length > 1) return { caption: `${cell.labels.length} смены`, tone: 'shift' };
  if (cell.labels.length === 1) return { caption: cell.labels[0].text, tone: 'shift' };
  if (cell.isDayOff) return { caption: 'Выходной', tone: 'off' };
  return { caption: '—', tone: 'none' };
};

/* Короткая подпись дня для полосы: столько человек на смене. Ноль пишем
   прочерком — «0 чел.» в полосе из семи дней читается хуже пустоты. */
export const plannerPhoneStripCaption = (count) => (Number(count) > 0 ? `${count} чел.` : '—');

/*
 * Сводка периода по одному человеку: смен, часов, выходных. Ночная смена
 * относится к дню начала, поэтому период считается по собственным сменам дней.
 */
export const plannerPhoneOperatorSummary = (op, range) => {
  const days = Array.isArray(range) ? range : [];
  let shifts = 0;
  let workMin = 0;
  let daysOff = 0;
  days.forEach((dateStr) => {
    const own = shiftsOf(op, dateStr);
    shifts += own.length;
    workMin += own.reduce((acc, shift) => acc + shiftMinutes(shift), 0);
    if (!own.length && Array.isArray(op?.daysOff) && op.daysOff.includes(dateStr)) daysOff += 1;
  });
  return { shifts, workMin, daysOff };
};

/* Та же сводка по всей выборке — строка под заголовком раздела. */
export const plannerPhonePeriodSummary = (operators, range) => {
  const list = Array.isArray(operators) ? operators : [];
  return list.reduce((acc, op) => {
    const item = plannerPhoneOperatorSummary(op, range);
    return {
      people: acc.people + 1,
      shifts: acc.shifts + item.shifts,
      workMin: acc.workMin + item.workMin,
      daysOff: acc.daysOff + item.daysOff,
    };
  }, { people: 0, shifts: 0, workMin: 0, daysOff: 0 });
};

/* Сколько человек выходит в этот день — подпись дня в полосе и число в шапке. */
export const plannerPhoneOnShiftCount = (operators, dateStr) => (Array.isArray(operators) ? operators : [])
  .filter((op) => shiftsOf(op, dateStr).length > 0).length;

/*
 * Люди сгруппированы по направлению — как строки настольной сетки, которая
 * отсортирована «направление, потом ФИО». Порядок внутри группы не трогаем:
 * его уже задал раздел (в «Дне» это может быть сортировка по началу смены).
 */
export const groupPlannerPhoneOperators = (operators) => {
  const groups = [];
  const byKey = new Map();
  (Array.isArray(operators) ? operators : []).forEach((op) => {
    // Строки СВ (задача #352) — своей группой, как в фильтре «Направления».
    const isSupervisor = ['sv', 'supervisor'].includes(String(op?.role || '').trim().toLowerCase());
    const label = (isSupervisor ? 'Супервайзеры' : String(op?.direction || '').trim()) || 'Без направления';
    if (!byKey.has(label)) {
      const group = { key: label, label, items: [] };
      byKey.set(label, group);
      groups.push(group);
    }
    byKey.get(label).items.push(op);
  });
  return groups;
};

const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_NOMINATIVE = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

const parseIso = (value) => {
  const [y, m, d] = String(value || '').split('-').map(Number);
  if (!y || !m || !d) return null;
  return { y, m, d };
};

/* Подпись периода над полосой: «18 сентября», «15 — 21 сентября»,
   «Сентябрь 2026». На стыке месяцев неделя подписывается обоими. */
export const plannerPhonePeriodLabel = (viewMode, range) => {
  const days = Array.isArray(range) ? range : [];
  const first = parseIso(days[0]);
  const last = parseIso(days[days.length - 1]);
  if (!first) return '';
  if (viewMode === 'month') return `${MONTHS_NOMINATIVE[first.m - 1]} ${first.y}`;
  if (viewMode === 'day' || !last || (first.d === last.d && first.m === last.m)) {
    return `${first.d} ${MONTHS_GENITIVE[first.m - 1]}`;
  }
  if (first.m === last.m && first.y === last.y) {
    return `${first.d} — ${last.d} ${MONTHS_GENITIVE[first.m - 1]}`;
  }
  return `${first.d} ${MONTHS_GENITIVE[first.m - 1]} — ${last.d} ${MONTHS_GENITIVE[last.m - 1]}`;
};

/* Выбранный день обязан лежать в видимом периоде: период листают стрелками, и
   день из прошлой недели там ничему не соответствует. */
export const pickPlannerPhoneDate = (selected, range, today) => {
  const days = Array.isArray(range) ? range : [];
  if (!days.length) return selected || today || '';
  if (days.includes(selected)) return selected;
  if (days.includes(today)) return today;
  return days[0];
};

/*
 * Что написать в строке отбора справа. Одного выбранного показываем по имени —
 * так видно, кто именно отобран, без раскрытия экрана; иначе «Выбрано: N».
 */
export const plannerPhoneFilterValue = (values, options, emptyLabel) => {
  const list = Array.isArray(values) ? values : [];
  if (!list.length) return emptyLabel;
  if (list.length === 1) {
    const found = (Array.isArray(options) ? options : []).find((opt) => String(opt?.value) === String(list[0]));
    if (found) return String(found.label);
  }
  return `Выбрано: ${list.length}`;
};

/* Список отбора: сначала уже отмеченные, потом остальные — иначе выбранный
   человек уезжает вниз, и на экране не видно, что отбор вообще действует. */
export const sortPlannerPhoneOptions = (options, values, query = '') => {
  const selected = new Set((Array.isArray(values) ? values : []).map(String));
  const needle = String(query || '').trim().toLowerCase();
  const list = (Array.isArray(options) ? options : []).filter((opt) => (
    !needle || String(opt?.label || '').toLowerCase().includes(needle)
  ));
  return [
    ...list.filter((opt) => selected.has(String(opt?.value))),
    ...list.filter((opt) => !selected.has(String(opt?.value))),
  ];
};
