/*
 * Интервал по умолчанию для выбранной даты в запросе на замену («Мои смены»).
 *
 * Зачем отдельным модулем: выбор даты живёт в трёх местах (предзаполнение формы,
 * селект на компьютере, селект на телефоне), и все три обязаны ставить время ТОЙ
 * ЖЕ даты. Пока правило было размазано, селект менял только дату — в форме
 * оставалось время прошлого дня, интервал выпадал из смен, и список кандидатов
 * молча оставался пустым. Та же мысль, что у setShiftChangeDate в «Запросах».
 *
 * Без React и без сети: на входе карта «день → смены» из /api/work_schedules/my.
 */

const MINUTES_IN_DAY = 1440;

const parseDayKey = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || '').trim());
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return Number.isNaN(date.getTime()) ? null : date;
};

const formatDayKey = (date) => (
    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
);

/** Соседний день ключом «YYYY-MM-DD»; пустая строка, если дата не разобралась. */
export const shiftDayKey = (dayKey, offset = 0) => {
    const date = parseDayKey(dayKey);
    if (!date) return '';
    date.setDate(date.getDate() + Number(offset || 0));
    return formatDayKey(date);
};

const timeToMinutes = (value) => {
    const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || '').trim());
    if (!match) return NaN;
    const hours = Number(match[1]);
    const minutes = Number(match[2]);
    if (hours > 23 || minutes > 59) return NaN;
    return hours * 60 + minutes;
};

const minutesToTime = (value) => {
    const wrapped = ((Math.round(value) % MINUTES_IN_DAY) + MINUTES_IN_DAY) % MINUTES_IN_DAY;
    return `${String(Math.floor(wrapped / 60)).padStart(2, '0')}:${String(wrapped % 60).padStart(2, '0')}`;
};

const shiftsForDay = (shiftsByDate, dayKey) => {
    if (!shiftsByDate || typeof shiftsByDate !== 'object' || !dayKey) return [];
    const list = shiftsByDate[dayKey];
    return Array.isArray(list) ? list : [];
};

/**
 * Смены дня в минутах от его полуночи. Ночной хвост не обрезается, а уезжает
 * за 1440: смена 18:30 — 01:00 это {1110, 1500}.
 */
export const ownShiftIntervalsForDate = (shiftsByDate, dayKey) => {
    const intervals = [];
    shiftsForDay(shiftsByDate, String(dayKey || '').trim()).forEach(seg => {
        const start = timeToMinutes(seg?.start);
        let end = timeToMinutes(seg?.end);
        if (!Number.isFinite(start) || !Number.isFinite(end)) return;
        if (end <= start) end += MINUTES_IN_DAY;
        if (end > start) intervals.push({ start, end });
    });
    intervals.sort((a, b) => (a.start - b.start) || (a.end - b.end));
    return intervals;
};

/**
 * Хвост вчерашней смены, доехавший в этот день: смена 15:00 — 01:00 отдаёт
 * {0, 60}. День с одним только хвостом тоже предлагают передать, и передавать
 * там нечего, кроме него.
 */
export const nightTailIntervalForDate = (shiftsByDate, dayKey) => {
    const prevKey = shiftDayKey(dayKey, -1);
    if (!prevKey) return null;
    let tailEnd = 0;
    ownShiftIntervalsForDate(shiftsByDate, prevKey).forEach(interval => {
        const end = interval.end - MINUTES_IN_DAY;
        if (end > tailEnd) tailEnd = end;
    });
    return tailEnd > 0 ? { start: 0, end: Math.min(tailEnd, MINUTES_IN_DAY) } : null;
};

/**
 * Что подставить в форму, когда выбрали дату: первая смена этого дня, а если
 * своих смен нет — ночной хвост предыдущей. Конец подрезаем часом (capMinutes),
 * как при первом открытии формы: целую смену берут тапом по полосе смены.
 *
 * Возвращает поля формы как есть: {endDate, startTime, endTime}.
 */
export const defaultSwapIntervalForDate = (shiftsByDate, dayKey, capMinutes = 60) => {
    const dateStr = String(dayKey || '').trim();
    if (!dateStr) return { endDate: '', startTime: '', endTime: '' };

    const base = ownShiftIntervalsForDate(shiftsByDate, dateStr)[0]
        || nightTailIntervalForDate(shiftsByDate, dateStr);
    if (!base) return { endDate: dateStr, startTime: '', endTime: '' };

    const cap = Number(capMinutes);
    const end = Number.isFinite(cap) && cap > 0 ? Math.min(base.end, base.start + cap) : base.end;
    return {
        endDate: end >= MINUTES_IN_DAY ? shiftDayKey(dateStr, 1) : dateStr,
        startTime: minutesToTime(base.start),
        endTime: minutesToTime(end)
    };
};
