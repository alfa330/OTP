/*
 * Арифметика формы «Заявка по смене» и подписи заявок (задача #349).
 *
 * Вынесено из App.jsx ради тестов: внутри компонента эти расчёты были
 * замыканиями, и проверить «сдвинул смену на два часа — что покраснеет на
 * полосе» можно было только глазами на стенде. Здесь нет ни состояния, ни
 * правил доступа — только минуты и строки. Решает по-прежнему сервер
 * (database.py: _validate_shift_change_move, _validate_shift_change_day_off);
 * форма повторяет его проверки затем, чтобы человек узнал об отказе до
 * отправки, а не после.
 *
 * Координаты везде одни: минуты от полуночи дня, конец ночной смены уходит за
 * 1440, интервалы полуоткрытые — смены, соприкасающиеся краями, не пересекаются.
 */

/** 'ГГГГ-ММ-ДД' со сдвигом на offset дней. Считаем в UTC: перевод часов не должен красть день. */
export function shiftDayKey(dayKey, offset = 0) {
  const [year, month, day] = String(dayKey || '').split('-').map(Number);
  if (!year || !month || !day) return '';
  const date = new Date(Date.UTC(year, month - 1, day + Number(offset || 0)));
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

/** Наступил ли момент «день + минуты от его полуночи», если сейчас nowKey и nowMinutes. */
export function momentPassed(dayKey, minutes, nowKey, nowMinutes) {
  if (!dayKey || !nowKey || !Number.isFinite(minutes)) return false;
  if (dayKey !== nowKey) return dayKey < nowKey;
  return minutes <= nowMinutes;
}

/** Пересекается ли [startMin, endMin) хоть с одним интервалом из busy. */
export function overlapsAny(busy, startMin, endMin) {
  return (busy || []).some((item) => item.startMin < endMin && startMin < item.endMin);
}

/**
 * Чем занят новый день переноса: его собственные смены, хвост вчерашней ночной
 * и утро завтрашнего дня — в минутах от его полуночи.
 *
 * shiftsForDate(dayKey) — смены дня, toMinutes(start, end) — их границы в
 * минутах или null. moved = { date, startMin, endMin } — сам переносимый кусок:
 * он в занятое не входит, сдвиг на пару часов законно пересекается со своим
 * прежним местом.
 */
export function occupiedAroundDay(targetDate, shiftsForDate, toMinutes, moved = null) {
  const result = [];
  if (!targetDate) return result;
  [-1, 0, 1].forEach((offset) => {
    const dayKey = shiftDayKey(targetDate, offset);
    (shiftsForDate(dayKey) || []).forEach((item) => {
      const bounds = toMinutes(item?.start, item?.end);
      if (!bounds) return;
      if (moved && dayKey === moved.date
        && bounds.startMin === moved.startMin && bounds.endMin === moved.endMin) return;
      result.push({
        startMin: bounds.startMin + offset * 1440,
        endMin: bounds.endMin + offset * 1440,
      });
    });
  });
  return result;
}

/**
 * Что освобождается от старого места смены seg, когда она встаёт на proposed
 * (оба — в одном дне). Кусков до двух: новое место может лечь в середину
 * старого. Не пересеклись вовсе — освобождается всё старое место.
 */
export function freedByMove(seg, proposed) {
  if (!seg || !proposed) return [];
  if (proposed.endMin <= seg.startMin || proposed.startMin >= seg.endMin) {
    return [{ start: seg.startMin, end: seg.endMin }];
  }
  return [
    { start: seg.startMin, end: proposed.startMin },
    { start: proposed.endMin, end: seg.endMin },
  ].filter((part) => part.end > part.start);
}

/**
 * Полосы таймлайна переноса. Ось — новый день: серым его остальные смены
 * (вместе с хвостом вчерашней ночной), розовым — освободившаяся часть старого
 * места, синим — новое место. При переносе в другой день старого места на этой
 * оси нет. Завтрашние смены видны только в той части, до которой ось
 * дотягивается сама: иначе одна утренняя смена растянула бы её на двое суток.
 */
export function moveTimeline({ seg, proposed, occupied = [], movesDay = false, conflict = false }) {
  const axisEnd = Math.max(
    1440,
    proposed.endMin,
    ...occupied.filter((item) => item.startMin < 1440).map((item) => item.endMin),
  );
  const base = occupied
    .map((item) => ({ start: Math.max(0, item.startMin), end: Math.min(axisEnd, item.endMin) }))
    .filter((part) => part.end > part.start);
  return {
    base,
    cut: movesDay ? [] : freedByMove(seg, proposed),
    add: [{ start: proposed.startMin, end: proposed.endMin }],
    addTone: 'blue',
    conflict,
  };
}

/**
 * Что именно просят — одной строкой, для списка оператора и очереди
 * руководителя. Считаем из payload, а не из minutesBefore/After: человеку
 * нужны времена, а не только дельта. formatDay — «14 окт.» из 'ГГГГ-ММ-ДД'.
 */
export function requestIntervalText(item, formatDay = (value) => String(value || '')) {
  const payload = item?.payload || {};
  const span = (start, end) => `${start || '—'} — ${end || '—'}`;
  if (item?.kind === 'extra') return span(payload.start, payload.end);
  if (item?.kind === 'day_off') {
    // Что снимется: все смены дня из снимка заявки.
    const shifts = Array.isArray(payload.shifts) ? payload.shifts : [];
    return shifts.length ? shifts.map((seg) => span(seg?.start, seg?.end)).join(', ') : '—';
  }
  const seg = payload.segment || {};
  // Перенос в другой день: дата нового места стоит перед его временем, дата
  // старого — перед всей строкой, как у остальных видов.
  const movedTo = item?.kind === 'change' && payload.newDate && payload.newDate !== item?.shiftDate
    ? `${formatDay(payload.newDate)} · `
    : '';
  return `${span(seg.start, seg.end)} → ${movedTo}${span(payload.newStart, payload.newEnd)}`;
}
