/* Статус офиса за выбранный день — одно правило на карточку, таблицу и легенду.
 *
 * Порядок ответов важнее самих ответов, поэтому он здесь один на всех:
 *   1. «Офиса в городе нет» — свойство записи, спорить не с чем;
 *   2. отметка человека за этот день («закрыт, прорвало трубу») — она и есть
 *      причина, по которой раздел появился: в графике временное закрытие не
 *      выразить;
 *   3. ночной снимок за этот день — что зафиксировали в момент, когда день ещё
 *      был сегодняшним;
 *   4. недельный график — расчёт, когда за день ничего не записано.
 *
 * Пометки «только по телефону» у офиса больше нет: телефон без офиса теперь
 * заводится на стороне парка («Онлайн — без офиса» в его номерах), и держать
 * то же самое второй записью значило бы снова разводить источники правды.
 *
 * Пункты 2 и 3 приезжают одним полем office.day и различаются source:
 * 'manual' против 'auto'.
 */

import { officeStatusOn } from './officeSchedule';

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

/**
 * Статус офиса за день.
 * { state, label, note, source, recordedOn, from, until }
 *
 * source: 'record' — отметка человека, 'snapshot' — ночной снимок,
 * 'schedule' — расчёт по графику (тогда recordedOn пустой: за этот день ничего
 * не фиксировали, и дата «обновлено» была бы выдумкой).
 */
export function officeDayStatus(office, dayISO) {
    if (office?.no_office) {
        return { state: 'absent', label: DAY_STATE_LABELS.absent, source: 'record', recordedOn: null };
    }

    const day = office?.day;
    if (day?.state) {
        return {
            state: day.state,
            label: DAY_STATE_LABELS[day.state] || DAY_STATE_LABELS.none,
            note: day.note || null,
            source: day.source === 'manual' ? 'record' : 'snapshot',
            recordedOn: day.recorded_on || null,
        };
    }

    const status = officeStatusOn(office?.schedule, dayISO);
    if (status.state === 'none') {
        return { state: 'none', label: DAY_STATE_LABELS.none, source: 'schedule', recordedOn: null };
    }
    return {
        state: status.state,
        label: DAY_STATE_LABELS[status.state],
        source: 'schedule',
        recordedOn: null,
        from: status.from,
        until: status.until,
    };
}
