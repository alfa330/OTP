/* Очередь ревью по дням — правила без React.
 *
 * Сервер отдаёт сводку дня (/api/ai-qa/review-queue/days) и строки дня
 * (/api/ai-qa/review-queue?day=…); здесь — подписи дня, склонения, какие
 * причины показывать у строки и как сводка меняется, когда человек только что
 * проверил разговор, а очередь ещё не перезагружалась. Проверяется node-тестом
 * tests/ai_qa_queue_day_rules.test.mjs.
 */

export const NO_DAY = 'none';

/** «1 разговор», «2 разговора», «5 разговоров». */
export const plural = (n, one, few, many) => {
    const abs = Math.abs(Number(n) || 0);
    const tail = abs % 100;
    const last = abs % 10;
    if (tail >= 11 && tail <= 14) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

const WEEKDAYS = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

const localIso = (date) => {
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};

/* Заголовок карточки дня. День сервера — дата разговора по Алматы, и у проверяющих
 * браузер в том же поясе, поэтому «сегодня» считаем по локальной дате устройства. */
export const dayTitle = (day, now = new Date()) => {
    if (!day || day === NO_DAY) return { title: 'Без даты', relative: null };
    const [y, m, d] = String(day).split('-').map(Number);
    const date = new Date(y, m - 1, d);
    if (Number.isNaN(date.getTime())) return { title: String(day), relative: null };
    const today = localIso(now);
    const yesterday = localIso(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
    const relative = day === today ? 'Сегодня' : day === yesterday ? 'Вчера' : null;
    const sameYear = y === now.getFullYear();
    return {
        title: `${WEEKDAYS[date.getDay()]}, ${d} ${MONTHS[m - 1]}${sameYear ? '' : ` ${y}`}`,
        relative,
    };
};

/** Время из подписи строки «23.09 12:04»; у заявки Chat2Desk времени нет. */
export const timeOf = (datetime) => {
    const match = String(datetime || '').match(/(\d{1,2}:\d{2})\s*$/);
    return match ? match[1] : null;
};

export const itemKey = (item) => `${item?.subject || 'call'}-${item?.id}`;

/* Причины, которые стоят у КАЖДОГО ждущего разговора дня, ничего не различают
 * («Данные ПО» у СЗоВ есть на всех карточках — критерии проверяются по ПО). На
 * карточке дня они названы один раз («у всех»), а у строк не повторяются. У дня с
 * одним разговором это все его причины — и в строке второй раз они тоже не нужны. */
export const commonReasons = (day) => Object.entries(day?.reasons || {})
    .filter(([key, n]) => key !== 'ok' && day.open > 0 && n >= day.open)
    .map(([key]) => key);

export const rowReasons = (reasons, common) => {
    const skip = new Set(common || []);
    const left = (reasons || []).filter((key) => !skip.has(key));
    return left.length ? left : (reasons || []).filter((key) => key === 'ok');
};

/* Только что проверенные разговоры (карточка закрылась после сохранения) — до
 * перезагрузки очереди. Сводка дня честно меняется на месте: ждут на один меньше,
 * проверено на один больше. День, где проверили всех, остаётся — с отметкой, что
 * всё проверено, — чтобы было видно, чем закончилась работа. */
export const applyReviewed = (days, reviewed) => {
    const byDay = new Map();
    (reviewed || []).forEach((entry) => {
        if (!entry?.day) return;
        const list = byDay.get(entry.day) || [];
        list.push(entry);
        byDay.set(entry.day, list);
    });
    return (days || []).map((day) => {
        const done = byDay.get(day.day) || [];
        if (!done.length) return day;
        const reasons = { ...(day.reasons || {}) };
        done.forEach((entry) => (entry.reasons || []).forEach((key) => {
            if (reasons[key]) reasons[key] -= 1;
            if (!reasons[key]) delete reasons[key];
        }));
        return {
            ...day,
            open: Math.max(0, day.open - done.length),
            critical: reasons.critical || 0,
            reasons,
            reviewed: Math.min(day.evaluated || 0, (day.reviewed || 0) + done.length),
            corrected: (day.corrected || 0) + done.filter((entry) => entry.corrected).length,
        };
    });
};

/** Сводка над карточками: сколько ждёт, за сколько дней, сколько критических. */
export const queueSummary = (days) => {
    const list = days || [];
    const open = list.reduce((sum, day) => sum + (day.open || 0), 0);
    const critical = list.reduce((sum, day) => sum + (day.open ? day.critical || 0 : 0), 0);
    const withOpen = list.filter((day) => day.open > 0).length;
    return { open, critical, days: withOpen };
};
