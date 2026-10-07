/* Объявление Oktell за интервалом «Тренинг» в «Графиках работы» (задача #382).
 *
 * Обязательное объявление, отправленное в Oktell, на время чтения ставит
 * оператору перерыв «Тренинг». В окне дня это обычный интервал статуса, и
 * супервайзер подтверждал его вслепую. Здесь — правила, по которым интервалу
 * находится объявление и по которым подтверждение ложится в «Тренинги».
 *
 * Модуль чистый и без импортов намеренно: его целиком исполняет тест
 * (tests/training_news.test.mjs), а правила тут про чужие часы — сверять их
 * глазами по экрану нельзя.
 */

/* Под какой темой подтверждённое чтение объявления уходит в «Тренинги».
 * Решение владельца 07.10.2026: отдельной темы не заводим — подставляем
 * существующую, а название объявления кладём в комментарий. Супервайзер тему
 * видит до сохранения и может сменить. */
export const NEWS_TRAINING_REASON = 'Тренинг по продукту';

const DAY_SEC = 24 * 60 * 60;
const DAY_MIN = 24 * 60;

/* Часы АТС и портала расходятся на секунду (замер 07.10.2026: медиана −1 с и
 * у начала, и у конца). Допуск именно секунды, не минуты: очередь объявлений
 * идёт подряд, окно следующего открывается через секунду после предыдущего, и
 * щедрый допуск приписывал интервалу чужое объявление. */
const CLOCK_SKEW_SEC = 2;
/* Неподтверждённое окно — точка показа: статус ставится за секунду до неё. */
const SHOWN_POINT_TOLERANCE_SEC = 5;

const finite = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
};

/* Ответ ручки /api/work_schedules/training_news → окна в одном виде.
 * Окно без названия или без времени показа выбрасывается: подписать интервал
 * нечем. Конец раньше начала читается как «не подтверждено». */
export function normalizeNewsWindows(items) {
    return (Array.isArray(items) ? items : [])
        .map((item) => {
            const startSec = finite(item?.start_sec ?? item?.startSec);
            const title = String(item?.title ?? '').trim();
            if (startSec === null || !title) return null;
            const endRaw = item?.end_sec ?? item?.endSec;
            const endSec = endRaw === null || endRaw === undefined ? null : finite(endRaw);
            return {
                newsId: item?.news_id ?? item?.newsId ?? null,
                title,
                startSec,
                endSec: endSec !== null && endSec >= startSec ? endSec : null,
            };
        })
        .filter(Boolean)
        .sort((left, right) => left.startSec - right.startSec);
}

/* Границы интервала статуса в секундах от полуночи. Отрезки окна дня несут
 * секунды; минуты — запасной путь для интервала, собранного руками. */
function segmentBounds(segment) {
    let start = finite(segment?.startSec);
    let end = finite(segment?.endSec);
    if (start === null || end === null) {
        const startMin = finite(segment?.startMin);
        const endMin = finite(segment?.endMin);
        if (startMin === null || endMin === null) return null;
        start = startMin * 60;
        end = endMin * 60;
    }
    if (end <= start) return null;
    return { start, end };
}

/* Объясняет ли окно объявления интервал статуса.
 *
 * Подтверждённое окно — отрезок «показ…подтверждение». Совпадением считается
 * случай, когда КОРОТКИЙ из двух (окно или интервал) хотя бы наполовину лежит
 * внутри длинного. Простое «пересекаются» не годится в обе стороны:
 *  - окно следующего объявления касается интервала предыдущего краем;
 *  - окно, открытое вечером и подтверждённое утром, длиннее любого интервала —
 *    и объясняет оба: вечерний и утренний.
 *
 * Неподтверждённое окно — только момент показа. Считать его «открытым до сих
 * пор» нельзя: снятое без подтверждения объявление подписало бы собой все
 * будущие тренинги человека. */
function windowExplains(win, bounds) {
    if (win.endSec === null || win.endSec <= win.startSec) {
        return win.startSec >= bounds.start - SHOWN_POINT_TOLERANCE_SEC
            && win.startSec <= bounds.end + SHOWN_POINT_TOLERANCE_SEC;
    }
    const overlap = Math.min(bounds.end + CLOCK_SKEW_SEC, win.endSec)
        - Math.max(bounds.start - CLOCK_SKEW_SEC, win.startSec);
    if (overlap <= 0) return false;
    const shorter = Math.min(bounds.end - bounds.start, win.endSec - win.startSec);
    return overlap >= Math.max(1, shorter) / 2;
}

/* Объявления, которые оператор читал в этот интервал. Их бывает несколько:
 * вернувшемуся с выходных очередь показывают подряд, а соседние отрезки
 * статуса окно дня склеивает в один. */
export function newsForSegment(segment, windows) {
    const bounds = segmentBounds(segment);
    if (!bounds) return [];
    const seen = new Set();
    const matches = [];
    (Array.isArray(windows) ? windows : []).forEach((win) => {
        const key = win.newsId ?? win.title;
        if (seen.has(key) || !windowExplains(win, bounds)) return;
        seen.add(key);
        matches.push(win);
    });
    return matches;
}

/* Комментарий записи в «Тренингах». Название — единственное, по чему потом
 * видно, что это было чтение объявления, а не занятие по продукту. Этой же
 * строкой объявления дня подписаны на телефоне, где интервалов по одному нет. */
export function buildNewsComment(matches) {
    const titles = [];
    (Array.isArray(matches) ? matches : []).forEach((match) => {
        const title = String(match?.title ?? '').trim();
        if (title && !titles.includes(title)) titles.push(title);
    });
    if (titles.length === 0) return '';
    return `Новость в Oktell: ${titles.map((title) => `«${title}»`).join('; ')}`;
}

const pad = (value) => String(value).padStart(2, '0');

/* Время с секундами — как у самого интервала, чтобы их можно было сверить
 * глазами. Окно, начатое не в этот день, подписывается словом: «вчера 18:59:10»
 * рядом с утренним интервалом объясняет, почему объявление «длилось» ночь. */
function clockWithDay(sec) {
    const day = Math.floor(sec / DAY_SEC);
    const rest = Math.round(sec - day * DAY_SEC);
    const clock = `${pad(Math.floor(rest / 3600))}:${pad(Math.floor((rest % 3600) / 60))}:${pad(rest % 60)}`;
    if (day === 0) return clock;
    if (day === -1) return `вчера ${clock}`;
    if (day === 1) return `завтра ${clock}`;
    return day < 0 ? `${-day} дн. назад ${clock}` : `через ${day} дн. ${clock}`;
}

export function formatNewsWindow(win) {
    if (!win || finite(win.startSec) === null) return '';
    if (win.endSec === null || win.endSec === undefined) {
        return `с ${clockWithDay(win.startSec)}, не подтверждена`;
    }
    return `${clockWithDay(win.startSec)} — ${clockWithDay(win.endSec)}`;
}

const minutesToClock = (minutes) => `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;

/* Во что превращаются интервалы статуса при подтверждении: записи «Тренингов»
 * с минутной точностью.
 *
 * Запись обязана накрыть СЕРЕДИНУ своего интервала — по ней окно дня решает,
 * подтверждён ли он. Поэтому начало округляется вниз, конец вверх. А дальше
 * минутные записи соседних интервалов начинают наезжать друг на друга, и
 * сервер наезд не принимает. Правила:
 *
 *  - уже сохранённый тренинг (`busy`) — стена: запись поджимается к его краю
 *    со своей стороны от середины;
 *  - следующий интервал начинается там, где кончился предыдущий, если его
 *    середина при этом остаётся накрытой;
 *  - иначе оба живут в одной минуте, и раздельно их не сохранить — они
 *    сливаются в одну запись, а объявления складываются.
 *
 * Последняя минута суток режется до 23:59 — у времени записи нет 24:00.
 *
 * Возвращает записи по порядку: {startMin, endMin, startTime, endTime, news}.
 */
export function planTrainingSaves(intervals, { windows = [], busy = [] } = {}) {
    const walls = (Array.isArray(busy) ? busy : [])
        .map((item) => ({ start: finite(item?.startMin), end: finite(item?.endMin) }))
        .filter((item) => item.start !== null && item.end !== null && item.end > item.start);

    const drafts = [];
    (Array.isArray(intervals) ? intervals : []).forEach((interval) => {
        const bounds = segmentBounds(interval);
        if (!bounds) return;
        const middle = (bounds.start + bounds.end) / 120;
        let startMin = Math.max(0, Math.floor(bounds.start / 60));
        let endMin = Math.min(DAY_MIN - 1, Math.ceil(bounds.end / 60));
        let covered = false;
        walls.forEach((wall) => {
            if (wall.end <= middle) startMin = Math.max(startMin, wall.end);
            else if (wall.start > middle) endMin = Math.min(endMin, wall.start);
            else covered = true;
        });
        // Середина уже под сохранённым тренингом — интервал подтверждён, второй
        // записи ему не нужно.
        if (covered || endMin <= startMin) return;
        drafts.push({
            startMin,
            endMin,
            middle,
            order: bounds.start,
            news: newsForSegment(interval, windows),
        });
    });

    drafts.sort((left, right) => left.order - right.order);

    const saves = [];
    drafts.forEach((draft) => {
        const last = saves[saves.length - 1];
        if (!last || draft.startMin >= last.endMin) {
            saves.push({ startMin: draft.startMin, endMin: draft.endMin, news: draft.news.slice() });
            return;
        }
        // Второе условие — про 23:59: срезанному концу суток сдвигаться некуда.
        if (draft.middle >= last.endMin && draft.endMin > last.endMin) {
            saves.push({ startMin: last.endMin, endMin: draft.endMin, news: draft.news.slice() });
            return;
        }
        last.endMin = Math.max(last.endMin, draft.endMin);
        draft.news.forEach((match) => {
            const known = last.news.some((item) => (item.newsId ?? item.title) === (match.newsId ?? match.title));
            if (!known) last.news.push(match);
        });
    });

    return saves.map((save) => ({
        ...save,
        startTime: minutesToClock(save.startMin),
        endTime: minutesToClock(save.endMin),
    }));
}
