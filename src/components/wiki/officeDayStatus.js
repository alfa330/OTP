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
 * Пункты 2 и 3 приезжают одним полем office.day и различаются source:
 * 'manual' против 'auto'.
 */

import { officeStatusOn } from './officeSchedule';

export const DAY_STATE_LABELS = {
    open: 'Открыт',
    closed: 'Закрыт',
    absent: 'Офиса в городе нет',
    online: 'Только по телефону',
    none: 'Нет графика',
};

/* Легенда ТЗ: три состояния, которые несут цвет. «Только по телефону» и «нет
 * графика» в легенду не идут — цветом они не кодируются, и строки в ней были бы
 * шумом. */
export const DAY_LEGEND = [
    { state: 'open', label: 'Открыт', dot: 'bg-emerald-500' },
    { state: 'closed', label: 'Закрыт', dot: 'bg-rose-500' },
    { state: 'absent', label: 'Офиса в городе нет', dot: 'bg-slate-500' },
];

export const DAY_STATE_TONE = {
    open: 'green', closed: 'red', absent: 'slate', online: 'blue', none: 'slate',
};

/* Заливка строки таблицы. Требование ТЗ буквальное: «строка окрашивается
 * целиком, чтобы проблемные и отсутствующие офисы были заметны сразу». */
export const DAY_STATE_ROW = {
    open: 'bg-emerald-50/70',
    closed: 'bg-rose-50/80',
    absent: 'bg-slate-200/70',
    online: '',
    none: '',
};

/** Кант слева на карточке: тот же цвет, но без заливки — двадцать полностью
 *  залитых карточек читаются как тревога, а не как справочник. */
export const DAY_STATE_EDGE = {
    open: 'before:bg-emerald-400',
    closed: 'before:bg-rose-400',
    absent: 'before:bg-slate-400',
    online: 'before:bg-blue-300',
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

    if (office?.is_online) {
        return { state: 'online', label: DAY_STATE_LABELS.online, source: 'record', recordedOn: null };
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
