/*
 * «Табло ОП», ТЗ #339: фильтр по группам и время входа. Отдельным модулем без импортов, чтобы
 * node-тест (tests/op_wallboard_groups.test.mjs) загружал его напрямую: opWallboardShared тянет
 * соседей путями без расширения, их понимает только сборщик.
 */

/*
 * Фильтр по группам (ТЗ #339): «Все / Основа / ЯР / Поток 1 / Поток 2». Разрезы считает сервер в том
 * же снимке (`groups`: итоги, часы, «сейчас»), поэтому экран не просит данных заново, а подменяет
 * куски снимка — плитки, график и список читают его как раньше и пересчитываются все разом.
 * Отбивка и виджет «поверх окон» по-прежнему про отдел целиком: фильтр — вид на экране, а не настройка.
 */
export const OP_GROUP_ALL = 'all';

export const opGroupOptions = (snapshot) => [
    { value: OP_GROUP_ALL, label: 'Все' },
    ...(snapshot?.groups || []).map((group) => ({ value: String(group.id), label: group.label })),
];

/** Выбранная группа из снимка; исчезнувшая из состава (или «Все») — null. */
export const opSelectedGroup = (snapshot, groupKey) => (
    (snapshot?.groups || []).find((group) => String(group.id) === String(groupKey)) || null
);

export const opGroupView = (snapshot, group) => {
    if (!snapshot || !group) return snapshot;
    return {
        ...snapshot,
        totals: group.totals,
        hourly: group.hourly,
        now: group.now,
        operators: (snapshot.operators || []).filter((row) => row.group_id === group.id),
    };
};

/*
 * Время входа: «08:28», а у ночной смены, вошедшей до полуночи, — «19:53» с пометкой «вчера»
 * (смена принадлежит дню своего начала, см. op_wallboard/snapshot.py). Сравнение дат строками:
 * обе в ISO, и часовой пояс у них один — Алматы, так что Date здесь только внёс бы сдвиг.
 */
export const opEntryTime = (entryAt, day) => {
    const match = String(entryAt || '').match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/);
    if (!match) return { time: '—', previousDay: false };
    return { time: `${match[2]}:${match[3]}`, previousDay: Boolean(day) && match[1] < String(day) };
};

/*
 * Ось графика «По часам» — одна на отдел и все группы: от первого часа со звонками отдела до
 * текущего часа снимка. Без общей оси у группы с двумя рабочими часами получались две плиты во
 * всю ширину, а при переключении группы столбики прыгали. Звонков ещё не было — null.
 */
export const opHourRange = (snapshot) => {
    const active = (snapshot?.hourly || []).filter((h) => h.arrived || h.outgoing).map((h) => h.hour);
    if (!active.length) return null;
    const match = String(snapshot?.captured_at || '').match(/T(\d{2}):/);
    const nowHour = match ? Number(match[1]) : -1;
    return { first: Math.min(...active), last: Math.max(...active, nowHour) };
};
