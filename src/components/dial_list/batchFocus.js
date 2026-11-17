/*
 * Точечная обработка базы (владелец, 17.11.2026): «удобный выбор загруженных баз на точечную обработку, с
 * возможностью выбрать несколько и отображать прогресс по каждой базе». Решения владельца в тот же день: в обоих
 * отделах раздела (Тез КЦ и Удалённый КЦ); операторы получают водителей ТОЛЬКО из отмеченных файлов; ничего не
 * отмечено — вся база месяца, как было.
 *
 * Чистая логика экрана «База водителей» → «Загрузки за месяц» (DialListSettings.jsx): прогресс файла, какие файлы
 * отмечены после нажатия, подписи. Сервер — dial_list/batch_focus.py, сводка leads/summary (batches[].focused,
 * batches[].progress, focus), смена отметок — PUT /api/dial_list/departments/<id>/batches/focus.
 */

/**
 * Этап журнала «База на паузе» (сервер — 'parked'): файл водителя не отмечен, а другие файлы месяца отмечены —
 * пул его не выдаст, пока файл не отметят. Точка полая: водитель не в работе, но и не закрыт. Чип — только когда
 * такие водители есть (hideIfZero): без отметок этапа не бывает, и пустой чип был бы шумом. Один объект на оба
 * журнала (удалённый КЦ — DialListJournal.STAGES, Тез — tezAutodial.TEZ_STAGES).
 */
export const PARKED_STAGE = Object.freeze({
    value: 'parked', label: 'База на паузе', dot: 'bg-white ring-1 ring-inset ring-slate-400', hideIfZero: true,
});

const count = (value) => {
    const n = Math.floor(Number(value));
    return Number.isFinite(n) && n > 0 ? n : 0;
};

/** Склонение по числу: 1 файл, 2 файла, 5 файлов, 21 файл. */
export const plural = (n, one, few, many) => {
    const abs = Math.abs(Number(n) || 0) % 100;
    const last = abs % 10;
    if (abs >= 11 && abs <= 14) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

/**
 * Прогресс файла из batch.progress сервера: total — водителей файла, open — ещё в обзвоне, goal — вышли на линию
 * (Тез) / подписали (удалённый КЦ), ready — можно выдать прямо сейчас. Обработано — total − open: обзвон по ним
 * закончен (цель, итог без повтора, попытки кончились, исключён). Процент не врёт по краям: 100 % — только когда
 * обработаны все, 0 % — только когда никто; посередине — ближайшее целое от 1 до 99.
 */
export const batchProgress = (batch) => {
    const p = batch?.progress || {};
    const total = count(p.total);
    const open = Math.min(count(p.open), total);
    const done = total - open;
    const goal = Math.min(count(p.goal), done);
    let pct = 0;
    if (total && done === total) pct = 100;
    else if (total && done) pct = Math.min(99, Math.max(1, Math.round((done * 100) / total)));
    return {
        total, open, done, goal, ready: count(p.ready), pct,
        goalShare: total ? goal / total : 0,
        restShare: total ? (done - goal) / total : 0,
    };
};

/** Отмеченные файлы списка (id строкой). */
export const focusedIds = (batches) => (Array.isArray(batches) ? batches : [])
    .filter((b) => b && b.focused && b.id != null)
    .map((b) => String(b.id));

/** Какие файлы будут отмечены после нажатия на файл id: был отмечен — снимается, нет — добавляется. */
export const toggledFocus = (batches, id) => {
    const key = String(id);
    const current = focusedIds(batches);
    return current.includes(key) ? current.filter((x) => x !== key) : [...current, key];
};

/** Список с отметками, которые ещё сохраняются (draft — id или null): экран показывает выбор сразу. */
export const withFocusDraft = (batches, draft) => {
    const list = Array.isArray(batches) ? batches : [];
    if (!Array.isArray(draft)) return list;
    const on = new Set(draft.map(String));
    return list.map((b) => ({ ...b, focused: on.has(String(b.id)) }));
};

/** Справа над списком: «В работе 2 из 5» — отмечено хоть что-то; иначе — что обзванивается вся база. */
export const focusStateText = (batches) => {
    const list = Array.isArray(batches) ? batches : [];
    const on = focusedIds(list).length;
    if (!on) return 'Обзванивается вся база';
    return `В работе ${on} из ${list.length}`;
};

/**
 * Предупреждение над списком: в отмеченных файлах сейчас звонить некому, а в остальных есть кому — операторы
 * стоят зря. Только у обзваниваемого месяца и только когда неотмеченные правда кого-то дали бы: пустая база —
 * это не про отметки, о ней экран говорит сам.
 */
export const focusWaitingText = (summary) => {
    if (!summary?.focus?.active || summary.is_active_period === false) return '';
    if (count(summary.pool_available) > 0) return '';
    const paused = count(summary.focus.paused_ready);
    if (!paused) return '';
    return `В отмеченных файлах сейчас звонить некому — операторы ждут. В остальных можно выдать ${paused} `
        + `${plural(paused, 'водителя', 'водителей', 'водителей')}: отметьте их или снимите отметки.`;
};

/** Что дописать к итогу загрузки, когда отмечены другие файлы: новый файл сам в работу не встаёт. */
export const uploadFocusNote = (summary) => (summary?.focus?.active
    ? 'Сейчас обзваниваются только отмеченные файлы — отметьте новый, чтобы операторы его получили'
    : '');

/** Цель файла словами отдела: у Тез — выход на линию, у удалённого КЦ — подписание. */
export const goalLabel = (isTez) => (isTez ? 'вышли на линию' : 'подписали');

/** Пояснение под «i» у заголовка списка. */
export const focusHint = (isTez) => [
    'Отметьте файлы, которые обзванивать сейчас: можно несколько.',
    'Отмечен хоть один — новые списки операторы получают только из отмеченных файлов; ничего не отмечено —',
    'обзванивается вся база месяца.',
    isTez
        ? 'Уже выданные списки операторы дорабатывают, а назначенные перезвоны и «дожать» звонят в срок.'
        : 'Уже выданные списки операторы дорабатывают.',
    `Процент — сколько водителей файла уже обработано: обзвон по ним закончен (дозвонились, ${goalLabel(isTez)},`,
    'попытки кончились или исключён).',
].join(' ');
