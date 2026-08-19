/* График работы офиса: разбор, свёртка в человеческие строки и живой статус.
 *
 * Хранение: { mon: {from, to, break_from, break_to} | null, ..., sun: ... },
 * где null — выходной. Ровно то же кладёт сервер (wiki/offices.py), сравнение
 * идёт по строкам «ЧЧ:ММ», поэтому часовых поясов внутри модели нет.
 *
 * Часовой пояс один на всю страну: Казахстан перешёл на UTC+5 целиком в марте
 * 2024 года, и офис в Актау живёт по тому же времени, что офис в Астане.
 * Считаем через Intl с явной зоной Asia/Almaty, а не через смещение браузера:
 * оператор может сидеть где угодно, а «открыто» относится к офису.
 */

export const DAY_CODES = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

export const DAY_LABELS = {
    mon: 'Пн', tue: 'Вт', wed: 'Ср', thu: 'Чт', fri: 'Пт', sat: 'Сб', sun: 'Вс',
};

const OFFICE_TIME_ZONE = 'Asia/Almaty';

const MINUTES_IN_DAY = 1440;

const toMinutes = (value) => {
    const found = /^(\d{1,2}):(\d{2})$/.exec(String(value || ''));
    if (!found) return null;
    const hours = Number(found[1]);
    const minutes = Number(found[2]);
    if (hours > 23 || minutes > 59) return null;
    return hours * 60 + minutes;
};

const fmt = (minutes) => {
    const total = ((minutes % MINUTES_IN_DAY) + MINUTES_IN_DAY) % MINUTES_IN_DAY;
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
};

/* Интервал дня в минутах от его полуночи. Закрытие раньше открытия читается
 * как «через полночь» и уезжает за 24:00 — иначе ночной офис выглядел бы
 * закрытым весь день. */
const dayInterval = (day) => {
    if (!day) return null;
    const from = toMinutes(day.from);
    let to = toMinutes(day.to);
    if (from === null || to === null || from === to) return null;
    if (to < from) to += MINUTES_IN_DAY;
    return { from, to };
};

const breakInterval = (day) => {
    if (!day) return null;
    const from = toMinutes(day.break_from);
    let to = toMinutes(day.break_to);
    if (from === null || to === null || from === to) return null;
    if (to < from) to += MINUTES_IN_DAY;
    return { from, to };
};

export const hasSchedule = (schedule) => (
    !!schedule && DAY_CODES.some((code) => dayInterval(schedule[code]))
);

/** Текущее время офиса: индекс дня (0 = понедельник) и минуты от полуночи. */
export function officeNow(now = new Date()) {
    const parts = new Intl.DateTimeFormat('en-GB', {
        timeZone: OFFICE_TIME_ZONE,
        weekday: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
    }).formatToParts(now);

    const value = (type) => parts.find((part) => part.type === type)?.value || '';
    const weekdays = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
    // '24' в роли полуночи встречается в некоторых движках при hour12: false.
    const hour = Number(value('hour')) % 24;
    return {
        dayIndex: weekdays[value('weekday')] ?? 0,
        minutes: hour * 60 + Number(value('minute')),
    };
}

/** Сегодня по времени офисов, «ГГГГ-ММ-ДД». Не по зоне браузера: у оператора в
 *  вебвью зона бывает чужой, а «сегодня» относится к офису. */
export function officeTodayISO(now = new Date()) {
    // en-CA даёт ровно ISO-порядок, без ручной сборки из частей.
    return new Intl.DateTimeFormat('en-CA', { timeZone: OFFICE_TIME_ZONE }).format(now);
}

/**
 * Статус офиса на текущий момент.
 * state: 'open' | 'break' | 'closed' | 'none' (график не заполнен).
 */
export function officeStatus(schedule, now = new Date()) {
    if (!hasSchedule(schedule)) return { state: 'none' };

    const { dayIndex, minutes } = officeNow(now);
    const dayAt = (offset) => schedule[DAY_CODES[(dayIndex + offset + 7) % 7]];

    // Вчерашний интервал, уехавший за полночь, ещё может быть открыт сейчас.
    for (const [offset, shift] of [[0, 0], [-1, MINUTES_IN_DAY]]) {
        const interval = dayInterval(dayAt(offset));
        if (!interval) continue;
        const at = minutes + shift;
        if (at < interval.from || at >= interval.to) continue;

        const lunch = breakInterval(dayAt(offset));
        if (lunch && at >= lunch.from && at < lunch.to) {
            return { state: 'break', until: fmt(lunch.to) };
        }
        return { state: 'open', until: fmt(interval.to) };
    }

    // Ближайшее открытие: сегодня позже или в один из следующих шести дней.
    for (let offset = 0; offset < 7; offset += 1) {
        const interval = dayInterval(dayAt(offset));
        if (!interval) continue;
        if (offset === 0 && minutes >= interval.from) continue;
        return {
            state: 'closed',
            opensAt: fmt(interval.from),
            opensDay: offset === 0 ? null : DAY_LABELS[DAY_CODES[(dayIndex + offset) % 7]],
        };
    }
    return { state: 'closed' };
}

/** Индекс дня недели (0 = понедельник) у даты «ГГГГ-ММ-ДД». null — не дата. */
export function dayIndexOf(dayISO) {
    const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dayISO || '').trim());
    if (!found) return null;
    // UTC-полдень, а не local: дата приходит как календарный день, и разбор по
    // местной зоне в западных поясах отдал бы предыдущие сутки.
    const date = new Date(Date.UTC(Number(found[1]), Number(found[2]) - 1, Number(found[3])));
    if (Number.isNaN(date.getTime())) return null;
    return (date.getUTCDay() + 6) % 7;
}

/**
 * Статус офиса за календарный день по недельному графику.
 * state: 'open' | 'closed' | 'none' (график не заполнен).
 *
 * Вердикт суточный: «работал ли офис в этот день». Про обед и «открыто до
 * 19:00» отвечает officeStatus, и только про сейчас — для прошедшего дня такая
 * точность была бы выдумкой. Близнец серверного schedule_state_on
 * (wiki/offices.py); расходиться им нельзя, потому оба под тестами.
 */
export function officeStatusOn(schedule, dayISO) {
    if (!hasSchedule(schedule)) return { state: 'none' };
    const index = dayIndexOf(dayISO);
    if (index === null) return { state: 'none' };

    const interval = dayInterval(schedule[DAY_CODES[index]]);
    if (!interval) return { state: 'closed' };
    return { state: 'open', from: fmt(interval.from), until: fmt(interval.to) };
}

const sameDay = (left, right) => {
    if (!left || !right) return left === right || (!left && !right);
    return ['from', 'to', 'break_from', 'break_to']
        .every((key) => (left[key] || null) === (right[key] || null));
};

/**
 * Сворачивает неделю в строки вида «Пн–Пт 09:00–19:00» / «Вс выходной».
 * Возвращает [{ days, time, isDayOff }].
 */
export function scheduleLines(schedule) {
    if (!schedule) return [];

    const groups = [];
    DAY_CODES.forEach((code) => {
        const day = dayInterval(schedule[code]) ? schedule[code] : null;
        const last = groups[groups.length - 1];
        if (last && sameDay(last.day, day)) last.codes.push(code);
        else groups.push({ day, codes: [code] });
    });

    return groups.map(({ day, codes }) => ({
        days: codes.length === 1
            ? DAY_LABELS[codes[0]]
            : `${DAY_LABELS[codes[0]]}–${DAY_LABELS[codes[codes.length - 1]]}`,
        time: day ? `${day.from}–${day.to}` : 'выходной',
        isDayOff: !day,
    }));
}

/**
 * Обед. Если он одинаковый во все рабочие дни — одна строка без перечисления
 * дней: в справочнике так у всех офисов, и «Пн–Вс 13:00–14:00» только шумит.
 */
export function breakLines(schedule) {
    if (!schedule) return [];

    const working = DAY_CODES
        .map((code) => ({ code, day: schedule[code] }))
        .filter(({ day }) => dayInterval(day));
    const withBreak = working.filter(({ day }) => breakInterval(day));
    if (withBreak.length === 0) return [];

    const first = withBreak[0].day;
    const uniform = withBreak.length === working.length
        && withBreak.every(({ day }) => day.break_from === first.break_from
            && day.break_to === first.break_to);
    if (uniform) return [{ days: null, time: `${first.break_from}–${first.break_to}` }];

    /* Дальше — сериями подряд идущих дней, а не строкой на день: у Костаная
       обед одинаков в Пн–Пт, и пять отдельных строк «обед Пн 13:00–14:00»
       читаются как пять разных перерывов. Свёртка та же, что у scheduleLines. */
    const runs = [];
    withBreak.forEach(({ code, day }) => {
        const time = `${day.break_from}–${day.break_to}`;
        const last = runs[runs.length - 1];
        const follows = last && DAY_CODES.indexOf(code)
            === DAY_CODES.indexOf(last.codes[last.codes.length - 1]) + 1;
        if (last && last.time === time && follows) last.codes.push(code);
        else runs.push({ time, codes: [code] });
    });

    return runs.map(({ time, codes }) => ({
        days: codes.length === 1
            ? DAY_LABELS[codes[0]]
            : `${DAY_LABELS[codes[0]]}–${DAY_LABELS[codes[codes.length - 1]]}`,
        time,
    }));
}

/** Разворачивает пресет формы («Пн-Пт 09:00-19:00») в полную неделю. */
export function buildSchedule({ days, from, to, breakFrom, breakTo }) {
    const result = {};
    DAY_CODES.forEach((code) => {
        if (!days.includes(code)) { result[code] = null; return; }
        const day = { from, to };
        if (breakFrom && breakTo) {
            day.break_from = breakFrom;
            day.break_to = breakTo;
        }
        result[code] = day;
    });
    return result;
}
