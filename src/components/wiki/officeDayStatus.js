/* Статус офиса за выбранный день — одно правило на карточку, таблицу и легенду.
 *
 * Порядок ответов важнее самих ответов, поэтому он здесь один на всех:
 *   1. «Офиса в городе нет» — свойство записи, спорить не с чем;
 *   2. отметка человека за ЭТОТ день («закрыт, прорвало трубу») — она и есть
 *      причина, по которой раздел появился: в графике временное закрытие не
 *      выразить;
 *   3. закрытие на СРОК («ремонт до 3 сентября») — заявление о череде дней;
 *      слабее отметки за конкретный день, потому что «сегодня всё-таки
 *      открыли» должно перебивать;
 *   4. ночной снимок за этот день — что зафиксировали в момент, когда день ещё
 *      был сегодняшним;
 *   5. недельный график — расчёт, когда за день ничего не записано.
 *
 * Пометки «только по телефону» у офиса больше нет: телефон без офиса теперь
 * заводится на стороне парка («Онлайн — без офиса» в его номерах), и держать
 * то же самое второй записью значило бы снова разводить источники правды.
 *
 * Пункты 2 и 4 приезжают одним полем office.day и различаются source:
 * 'manual' против 'auto'. Пункт 3 живёт на самой записи офиса
 * (closed_from / closed_until / closed_note).
 */

// С расширением: модуль читают и тесты через node --test, а там ESM без
// расширения не разрешается (carMatch.js импортирует так же).
import { officeStatusOn, untilText } from './officeSchedule.js';

export const DAY_STATE_LABELS = {
    open: 'Открыт',
    closed: 'Закрыт',
    absent: 'Офиса в городе нет',
    none: 'Нет графика',
};

export const DAY_STATE_TONE = {
    open: 'green', closed: 'red', absent: 'slate', none: 'slate',
};

/* Заливка строки таблицы. Требование ТЗ буквальное: «строка окрашивается
 * целиком, чтобы проблемные и отсутствующие офисы были заметны сразу».
 *
 * Веса сняты пипеткой с макета ТЗ (открыт #E1F5EE, закрыт #FCEBEB, нет офиса
 * #D3D1C7) и выражены палитрой раздела: макет тёплый, портал холодный, и
 * бежевая строка среди slate-панелей читалась бы как чужая вставка. Прежние
 * emerald-50/70 и slate-200/70 в цвет макета не попадали: заливка была вдвое
 * бледнее и «залитая целиком строка» на экране выглядела оттенком белого.
 * «Нет графика» цвета не несёт вовсе — за этот день ничего не известно. */
export const DAY_STATE_ROW = {
    open: 'bg-emerald-100/60',
    closed: 'bg-rose-100/70',
    absent: 'bg-slate-300',
    none: 'bg-white',
};

/* Цвет ТЕКСТА строки, а не только фона. В макете тонирована вся строка: адрес
 * открытого офиса тёмно-зелёный (#08503F), дата — #0F6E56. Нейтральный
 * slate-700 на цветной заливке смотрится наклейкой поверх строки, поэтому
 * колонки берут тон состояния.
 *
 * Город остаётся почти чёрным во всех состояниях (в макете тоже): это ключ
 * строки, по нему ведут глазами, и тонировать его — терять точку входа. */
export const DAY_STATE_TEXT = {
    open: { city: 'text-slate-900', body: 'text-emerald-800', meta: 'text-emerald-700' },
    closed: { city: 'text-slate-900', body: 'text-rose-800', meta: 'text-rose-700' },
    absent: { city: 'text-slate-800', body: 'text-slate-600', meta: 'text-slate-500' },
    none: { city: 'text-slate-900', body: 'text-slate-700', meta: 'text-slate-500' },
};

/* Бейдж статуса в залитой строке. IosBadge для этого не годится: его тона
 * (bg-emerald-50) светлее самой строки, и бейдж пропадал в заливке — ровно то,
 * от чего ТЗ уводит. Здесь тон на ступень плотнее строки, как в макете
 * (#9FE1CB на #E1F5EE), и кружок повторяет легенду, чтобы цветовая кодировка
 * читалась одинаково в обоих местах. */
export const DAY_STATE_PILL = {
    open: { fill: 'bg-emerald-200 text-emerald-900', dot: 'bg-emerald-600' },
    closed: { fill: 'bg-rose-200 text-rose-900', dot: 'bg-rose-500' },
    absent: { fill: 'bg-slate-400/60 text-slate-800', dot: 'bg-slate-600' },
    none: { fill: 'bg-slate-100 text-slate-600', dot: 'bg-slate-300' },
};

/* Легенда ТЗ: три состояния, которые несут цвет. «Нет графика» в неё не идёт —
 * цветом оно не кодируется, и строка в легенде была бы шумом.
 *
 * Кружок берётся из бейджа, а не пишется рядом второй раз: легенда учит читать
 * цвет в строке, и разойдись эти два места на полтона — она бы этому и мешала. */
export const DAY_LEGEND = [
    { state: 'open', label: 'Открыт', dot: DAY_STATE_PILL.open.dot },
    { state: 'closed', label: 'Закрыт', dot: DAY_STATE_PILL.closed.dot },
    { state: 'absent', label: 'Офиса в городе нет', dot: DAY_STATE_PILL.absent.dot },
];

/** Кант слева на карточке: тот же цвет, но без заливки — двадцать полностью
 *  залитых карточек читаются как тревога, а не как справочник. */
export const DAY_STATE_EDGE = {
    open: 'before:bg-emerald-400',
    closed: 'before:bg-rose-400',
    absent: 'before:bg-slate-400',
    none: 'before:bg-slate-200',
};

/** '2026-08-19' → '19.08.2026'. Пустое или не дата → прочерк. */
export const formatDay = (dayISO) => {
    const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dayISO || '').trim());
    return found ? `${found[3]}.${found[2]}.${found[1]}` : '—';
};

/** Отметка времени сервера → '19.08.2026'. День ('2026-08-19') и время
 *  ('2026-08-19T23:45:02') принимаются одинаково: «обновлено» приезжает с
 *  минутами, а день недели — без, и звать для этого две функции незачем. */
export const formatStamp = (iso) => formatDay(String(iso || '').slice(0, 10));

/** То же с минутами: '19.08.2026, 23:45'. Зоны у отметки нет — сервер пишет
 *  её сразу по Алматы, и переводить время браузера было бы ошибкой. */
export const formatStampTime = (iso) => {
    const text = String(iso || '').trim();
    const day = formatStamp(text);
    if (day === '—') return '—';
    const time = /T(\d{2}):(\d{2})/.exec(text);
    return time ? `${day}, ${time[1]}:${time[2]}` : day;
};

/** Откуда взялось состояние строки — расшифровка source для колонки
 *  «Обновлено»: без неё дата не отвечает на вопрос «кто это записал». */
export const DAY_SOURCE_LABELS = {
    record: 'отметка дежурного',
    closure: 'закрытие на срок',
    snapshot: 'ночной снимок',
    schedule: 'правка справочника',
};

/**
 * Попадает ли день в закрытие на срок.
 *
 * Близнец серверного `closure_covers` (wiki/offices.py), границы те же:
 * closed_from включительно, closed_until — день ОТКРЫТИЯ, то есть не
 * включительно. Надпись «закрыт до 29.08» читается буквально: 28-го ещё
 * закрыт, 29-го работает. Пустой closed_until при заполненном closed_from —
 * «срок не известен».
 *
 * Сравнение строк, а не дат: 'ГГГГ-ММ-ДД' сортируется лексикографически ровно
 * как хронологически, и разбор в Date только добавил бы сюда часовые пояса.
 */
export const closureCovers = (office, dayISO) => {
    const from = office?.closed_from;
    const day = String(dayISO || '');
    if (!from || !/^\d{4}-\d{2}-\d{2}$/.test(day) || day < from) return false;
    const until = office?.closed_until;
    return !until || day < until;
};

/** '2026-08-29' → '29.08' в том же году и '29.08.2027' в другом: в таблице
 *  год — это четыре лишних знака в каждой строке, но соврать им нельзя. */
export const formatDayShort = (iso, sameYearAs) => {
    const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || '').trim());
    if (!found) return '—';
    const year = String(sameYearAs || '').slice(0, 4);
    return found[1] === year ? `${found[3]}.${found[2]}` : `${found[3]}.${found[2]}.${found[1]}`;
};

/**
 * Срок рядом со статусом — то, ради чего заведена задача #236.
 *
 * Порядок ответов повторяет порядок правил: закрытие на срок сильнее живого
 * расчёта по графику, потому что оно его и перебивает. `live` — результат
 * officeStatus(schedule), он есть только у сегодняшнего дня; за прошедший день
 * «до завтра 10:00» было бы выдумкой, там остаются лишь часы самого дня.
 */
export function statusUntil(status, live, dayISO) {
    if (!status) return null;
    if (status.openEnded) return 'срок не известен';
    if (status.closedUntil) return `до ${formatDayShort(status.closedUntil, dayISO)}`;
    // Живой расчёт годится только там, где состояние и взято из графика: поверх
    // ручной отметки «сегодня закрыто» он написал бы «до 19:00», потому что в
    // графике день рабочий. Отметка на то и ставится, что график тут неправ.
    if (live && status.source !== 'record') return untilText(live);
    // Прошедший день: суточный вердикт знает часы, но не минуту закрытия.
    return status.from && status.until ? `${status.from}–${status.until}` : null;
}

/**
 * Статус офиса за день.
 * { state, label, note, source, recordedOn, updatedAt, from, until }
 *
 * source: 'record' — отметка человека, 'snapshot' — ночной снимок,
 * 'schedule' — расчёт по графику.
 *
 * recordedOn — ЗА какой день запись, updatedAt — КОГДА данные строки последний
 * раз меняли. Это и есть колонка ТЗ «Обновлено», и она не может быть пустой у
 * работающего офиса: даже когда за день ничего не отмечали, состояние взято из
 * графика, а график лежит в записи офиса — значит, дата правки записи и есть
 * дата актуальности строки. Раньше сюда уезжал recordedOn, то есть колонка
 * повторяла выбранную в календаре дату, а у большинства строк стоял прочерк.
 *
 * Единственный прочерк, который остаётся, — «офиса в городе нет» (буква п. 4.3
 * ТЗ): обновлять там нечего.
 */
export function officeDayStatus(office, dayISO) {
    if (office?.no_office) {
        return {
            state: 'absent',
            label: DAY_STATE_LABELS.absent,
            source: 'record',
            recordedOn: null,
            updatedAt: null,
        };
    }

    const day = office?.day;
    const marked = day?.state && day.source === 'manual';
    if (marked) {
        return {
            state: day.state,
            label: DAY_STATE_LABELS[day.state] || DAY_STATE_LABELS.none,
            note: day.note || null,
            source: 'record',
            recordedOn: day.recorded_on || null,
            // Отметка старее правки справочника не бывает: её и ставят поверх
            // графика. Но если сервер отметку отдал без времени (старые строки
            // до появления recorded_at), падать на прочерк незачем.
            updatedAt: day.recorded_at || office?.updated_at || null,
        };
    }

    // Закрытие на срок — ниже ручной отметки за конкретный день («ремонт до
    // 3 сентября, но сегодня всё-таки открыли») и выше ночного снимка: снимок
    // считает по графику, а закрытие — это прямое утверждение человека об этих
    // днях. Иначе назавтра после отметки офис «открывался» сам, что и было на
    // проде 24.08.2026 с Атырау и Костанаем.
    if (closureCovers(office, dayISO)) {
        return {
            state: 'closed',
            label: DAY_STATE_LABELS.closed,
            note: office.closed_note || null,
            source: 'closure',
            recordedOn: null,
            updatedAt: office?.updated_at || null,
            closedUntil: office.closed_until || null,
            openEnded: !office.closed_until,
        };
    }

    if (day?.state) {
        // Снимок хранит только состояние. Часы того дня берём из графика: для
        // «Открыт» это ответ «до скольки работал», и он не выдумка — снимок и
        // считался по этому же графику.
        const hours = day.state === 'open' ? officeStatusOn(office?.schedule, dayISO) : null;
        return {
            state: day.state,
            label: DAY_STATE_LABELS[day.state] || DAY_STATE_LABELS.none,
            note: day.note || null,
            source: 'snapshot',
            recordedOn: day.recorded_on || null,
            updatedAt: day.recorded_at || office?.updated_at || null,
            from: hours?.from,
            until: hours?.until,
        };
    }

    const status = officeStatusOn(office?.schedule, dayISO);
    const base = {
        source: 'schedule',
        recordedOn: null,
        updatedAt: office?.updated_at || null,
    };
    if (status.state === 'none') {
        return { ...base, state: 'none', label: DAY_STATE_LABELS.none };
    }
    return {
        ...base,
        state: status.state,
        label: DAY_STATE_LABELS[status.state],
        from: status.from,
        until: status.until,
    };
}
