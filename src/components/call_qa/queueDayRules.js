/* Очередь ревью по дням — правила без React.
 *
 * Сервер отдаёт сводку дней (/api/ai-qa/review-queue/days) — из неё плитки дней,
 * сгруппированные по месяцам; разговоры дня — по нажатию на плитку
 * (/api/ai-qa/review-queue?day=…) отдельным экраном дня. Здесь — подписи дня и
 * месяца, склонения, какие причины показывать у строки, догрузка строк дня, соседние
 * дни и как сводка меняется, когда человек только что проверил разговор, а очередь
 * ещё не перезагружалась. Проверяется node-тестом tests/ai_qa_queue_day_rules.test.mjs.
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
const MONTHS_TITLE = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
                      'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

const localIso = (date) => {
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};

const parseDay = (day) => {
    if (!day || day === NO_DAY) return null;
    const [y, m, d] = String(day).split('-').map(Number);
    const date = new Date(y, m - 1, d);
    return Number.isNaN(date.getTime()) ? null : { y, m, d, date };
};

/* Подпись дня: «21 сентября» (год — только чужой), день недели и «Сегодня»/«Вчера»;
 * `title` — всё одной строкой. День сервера — дата разговора по Алматы, и у
 * проверяющих браузер в том же поясе, поэтому «сегодня» считаем по локальной дате
 * устройства. */
export const dayTitle = (day, now = new Date()) => {
    const parsed = parseDay(day);
    if (!parsed) {
        const label = !day || day === NO_DAY ? 'Без даты' : String(day);
        return { title: label, date: label, weekday: null, relative: null };
    }
    const { y, m, d, date } = parsed;
    const today = localIso(now);
    const yesterday = localIso(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
    const relative = day === today ? 'Сегодня' : day === yesterday ? 'Вчера' : null;
    const weekday = WEEKDAYS[date.getDay()];
    const label = `${d} ${MONTHS[m - 1]}${y === now.getFullYear() ? '' : ` ${y}`}`;
    return { title: `${weekday}, ${label}`, date: label, weekday, relative };
};

/* Плитки дней — по месяцам, как «Фото» и «Календарь»: у ОП в очереди дни за
 * четыре месяца, и сплошная сетка без заголовков читалась бы как одна простыня.
 * Порядок дней внутри месяца — как пришли (от свежих к старым), день без даты —
 * своей группой в конце. */
export const monthGroups = (days, now = new Date()) => {
    const groups = [];
    const byKey = new Map();
    (days || []).forEach((day) => {
        const parsed = parseDay(day.day);
        const key = parsed ? `${parsed.y}-${String(parsed.m).padStart(2, '0')}` : NO_DAY;
        let group = byKey.get(key);
        if (!group) {
            group = {
                key,
                title: parsed
                    ? `${MONTHS_TITLE[parsed.m - 1]}${parsed.y === now.getFullYear() ? '' : ` ${parsed.y}`}`
                    : 'Без даты',
                days: [], open: 0, critical: 0,
            };
            byKey.set(key, group);
            groups.push(group);
        }
        group.days.push(day);
        group.open += day.open || 0;
        group.critical += day.open ? day.critical || 0 : 0;
    });
    const dated = groups.filter((group) => group.key !== NO_DAY);
    return [...dated, ...groups.filter((group) => group.key === NO_DAY)];
};

/* Соседние дни очереди для стрелок экрана дня: `earlier` — день раньше, `later` —
 * позже (сводка идёт от свежих к старым). */
export const dayNeighbours = (days, day) => {
    const list = days || [];
    const at = list.findIndex((item) => item.day === day);
    if (at < 0) return { earlier: null, later: null };
    return { earlier: list[at + 1]?.day ?? null, later: list[at - 1]?.day ?? null };
};

/* Куда идти, когда в дне всё проверено: следующий по ленте день, где ещё кто-то
 * ждёт (раньше по времени), а если таких нет — ближайший более поздний. */
export const nextDayToReview = (days, day) => {
    const list = days || [];
    const at = list.findIndex((item) => item.day === day);
    const waiting = (item) => item && item.day !== day && item.open > 0;
    const after = list.slice(at + 1).find(waiting);
    if (after) return after.day;
    const before = list.slice(0, Math.max(0, at)).reverse().find(waiting);
    return before ? before.day : null;
};

/** Время из подписи строки «23.09 12:04»; у заявки Chat2Desk времени нет. */
export const timeOf = (datetime) => {
    const match = String(datetime || '').match(/(\d{1,2}:\d{2})\s*$/);
    return match ? match[1] : null;
};

export const itemKey = (item) => `${item?.subject || 'call'}-${item?.id}`;

/* Причины, которые стоят у КАЖДОГО ждущего разговора дня, ничего не различают:
 * у ОП почти у всех «Данные ПО» (критерии проверяются по ПО). На экране дня их
 * называют один раз над списком, а у строк не повторяют. Считается по сводке дня,
 * а не по загруженным строкам: у большого дня видны не все разговоры.
 *
 * «Критическое» общим не бывает никогда: это то, что открывают первым, и метка
 * обязана остаться у своей строки. Один разговор — тоже не «у всех».
 * «Без флагов» и «Новое» (режим совместимости) — не причины, а их отсутствие: их
 * не показывают нигде, пустая строка и так значит «ИИ поводов не нашёл». */
const SILENT = new Set(['ok', 'new']);

/* «Оценка устарела» — не причина очереди, а состояние оценки: после смены промпта
 * она стоит почти у всех. Сервер считает её только для отданных строк, поэтому
 * «у всех» решается по загруженным строкам (`rows`), а не по сводке: догруженная
 * свежая строка возвращает метку остальным. */
export const STALE_MARK = 'stale';

export const commonReasons = (days, rows = []) => {
    const list = days || [];
    const open = list.reduce((sum, day) => sum + (day.open || 0), 0);
    if (open < 2) return [];
    const counts = {};
    list.forEach((day) => {
        if (!day.open) return;
        Object.entries(day.reasons || {}).forEach(([key, n]) => { counts[key] = (counts[key] || 0) + n; });
    });
    const common = Object.keys(counts)
        .filter((key) => key !== 'critical' && !SILENT.has(key) && counts[key] >= open);
    const loaded = rows || [];
    if (loaded.length >= 2 && loaded.every((row) => row?.stale === true)) common.push(STALE_MARK);
    return common;
};

/* То, что у всех загруженных строк одинаково, в строке не повторяется: у СЗоВ все
 * звонки — из АТС и одного направления («Основа»), и эти слова стояли бы в каждой
 * строке. «Из АТС» нужно, только когда рядом есть и звонки журнала: id у них
 * независимые, и без пометки две строки читались бы как один звонок. Одна строка —
 * не «у всех». */
export const commonRowMeta = (rows) => {
    const list = rows || [];
    const same = (get) => list.length >= 2 && list.every((row) => get(row) === get(list[0]));
    return {
        subject: same((row) => row?.subject || 'call'),
        direction: same((row) => row?.direction || ''),
    };
};

export const rowReasons = (reasons, common) => {
    const skip = new Set(common || []);
    return (reasons || []).filter((key) => !SILENT.has(key) && !skip.has(key));
};

/* Строки дня: первая страница — при открытии дня, дальше «Показать ещё» — запрос с
 * перекрытием. Пока человек работает, другой проверяющий закрывает строки дня, а
 * «Переоценить» двигает строку в порядке дня, — и запрос ровно со смещения «сколько
 * загружено» молча пропускал бы разговор. Поэтому берём на DAY_PAGE_OVERLAP строк
 * раньше, а повторы отсеиваем по ключу. `loaded` — загруженные и ещё не проверенные
 * здесь строки: проверенные сервер уже не отдаёт. Больше 200 строк за раз сервер не
 * отдаёт. */
export const DAY_PAGE = 50;
export const DAY_PAGE_OVERLAP = 20;
const DAY_PAGE_MAX = 200;

export const dayPageRequest = (loaded) => {
    const count = Math.max(0, Number(loaded) || 0);
    const offset = Math.max(0, count - DAY_PAGE_OVERLAP);
    return { offset, limit: Math.min(DAY_PAGE_MAX, DAY_PAGE + (count - offset)) };
};

/* Ответ дня поверх загруженного: новые строки — в конец, повторы перекрытия —
 * мимо. `end` — докуда дочитали в нынешнем порядке сервера, `total` — сколько ждёт
 * в дне сейчас: по ним «Показать ещё» считает остаток. */
export const mergeDayPage = (items, page, offset, total) => {
    const base = items || [];
    const rows = page || [];
    const known = new Set(base.map(itemKey));
    return {
        items: [...base, ...rows.filter((item) => !known.has(itemKey(item)))],
        total: Number(total) || 0,
        end: offset + rows.length,
    };
};

/* Только что проверенные разговоры (карточка закрылась после сохранения) — до
 * перезагрузки очереди. Сводка дня честно меняется на месте: ждут на один меньше,
 * проверено на один больше, новый балл человека входит в средний. День, где
 * проверили всех, остаётся — с отметкой, что всё проверено, — чтобы было видно,
 * чем закончилась работа. */
const isScore = (value) => Number.isFinite(value) && value >= 0 && value <= 100;

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
        // null — балла нет (Number(null) дал бы 0 и уронил бы средний).
        const added = done.filter((entry) => entry.human != null && entry.human !== '')
            .map((entry) => Number(entry.human)).filter((value) => isScore(value));
        const humanN = (day.human_n || 0) + added.length;
        const humanSum = (day.human_avg != null ? day.human_avg * (day.human_n || 0) : 0)
            + added.reduce((sum, value) => sum + value, 0);
        return {
            ...day,
            open: Math.max(0, day.open - done.length),
            critical: reasons.critical || 0,
            reasons,
            reviewed: Math.min(day.evaluated || 0, (day.reviewed || 0) + done.length),
            corrected: (day.corrected || 0) + done.filter((entry) => entry.corrected).length,
            human_n: humanN,
            human_avg: humanN && (day.human_avg != null || added.length)
                ? Math.round((humanSum / humanN) * 10) / 10 : day.human_avg ?? null,
        };
    });
};

/** Сводка над плитками: сколько ждёт, за сколько дней, сколько критических. */
export const queueSummary = (days) => {
    const list = days || [];
    const open = list.reduce((sum, day) => sum + (day.open || 0), 0);
    const critical = list.reduce((sum, day) => sum + (day.open ? day.critical || 0 : 0), 0);
    const withOpen = list.filter((day) => day.open > 0).length;
    return { open, critical, days: withOpen };
};
