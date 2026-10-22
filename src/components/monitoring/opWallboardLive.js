/*
 * «Табло ОП» в реальном времени: слияние живых кадров потока /api/op_wallboard/live со снимком.
 *
 * Модуль без импортов — его проверяет node-тест (tests/op_wallboard_live.test.mjs).
 *
 * Снимок дня тяжёлый и приходит раз в TTL; поток шлёт кадр `live` при каждом изменении на
 * станции или в телефонах: живые статусы людей (`live`), статус телефона строки, «сейчас»
 * отдела и групп (вместе с очередью). Всё это СЕРВЕР уже посчитал (op_wallboard/snapshot.py,
 * live_patch) — здесь только подстановка по ключам, чтобы на экране не было второго расчёта,
 * который однажды разошёлся бы с отбивкой в Telegram.
 *
 * Кадр старше снимка (снимок собран позже кадра) не применяется: снимок уже знает больше.
 *
 * Часы. Времена станции и сервера — местные строки «YYYY-MM-DDTHH:MM:SS» без пояса. Экран ведёт
 * их в той же шкале: «сейчас сервера» = последняя присланная отметка + сколько прошло у браузера
 * с её получения. Ни часы браузера, ни его пояс в расчёт не входят.
 */

export const OP_LIVE_ROW_FIELDS = ['live', 'status_key', 'status_label', 'status_weight', 'status_seconds', 'status_at'];

/* Ключ строки — тот же, что у сервера (snapshot.row_key). */
export const opRowKey = (row) => (
    row?.id !== null && row?.id !== undefined ? String(row.id) : `ext:${row?.ext || ''}`
);

/* «YYYY-MM-DDTHH:MM:SS» (или через пробел, с долями) → секунды в «настенной» шкале. */
export const wallSeconds = (value) => {
    const text = String(value || '').trim().replace(' ', 'T').slice(0, 19);
    if (text.length !== 19) return null;
    const stamp = Date.parse(`${text}Z`);
    return Number.isNaN(stamp) ? null : Math.floor(stamp / 1000);
};

export const wallStamp = (seconds) => (
    Number.isFinite(seconds) ? new Date(seconds * 1000).toISOString().slice(0, 19) : null
);

/* Подставить кадр в снимок. receivedMs — когда пришёл снимок и когда кадр (для часов). */
export const applyOpLivePatch = (snapshot, patch, { snapshotReceivedMs = 0, patchReceivedMs = 0 } = {}) => {
    if (!snapshot) return snapshot;
    const anchors = [];
    const snapshotAt = snapshot.served_at || snapshot.captured_at;
    if (snapshotAt) anchors.push({ at: snapshotAt, receivedMs: snapshotReceivedMs });
    const fresh = Boolean(patch && patch.at && (!snapshot.captured_at || patch.at >= snapshot.captured_at));
    if (fresh) anchors.push({ at: patch.at, receivedMs: patchReceivedMs });
    const anchor = anchors.reduce((best, item) => (!best || item.receivedMs >= best.receivedMs ? item : best), null);
    if (!fresh) return { ...snapshot, clock_anchor: anchor };
    const byKey = patch.operators || {};
    const operators = (snapshot.operators || []).map((row) => {
        const update = byKey[opRowKey(row)];
        if (!update) return row;
        const next = { ...row };
        OP_LIVE_ROW_FIELDS.forEach((field) => {
            if (Object.prototype.hasOwnProperty.call(update, field)) next[field] = update[field];
        });
        return next;
    });
    const groupsNow = patch.groups_now || {};
    const groups = (snapshot.groups || []).map((group) => (
        groupsNow[String(group.id)] ? { ...group, now: groupsNow[String(group.id)] } : group
    ));
    return {
        ...snapshot,
        operators,
        groups,
        now: patch.now || snapshot.now,
        live: { ...(snapshot.live || {}), online: Boolean(patch.online), at: patch.at },
        clock_anchor: anchor,
    };
};

/* «Сейчас сервера» в секундах настенной шкалы на момент nowMs браузера. */
export const opClockNow = (snapshot, nowMs) => {
    const anchor = snapshot?.clock_anchor;
    const base = wallSeconds(anchor?.at);
    if (base === null) return null;
    return base + Math.max(0, Math.floor((nowMs - (anchor.receivedMs || nowMs)) / 1000));
};

/* Снимок с тикающими часами: новое значение clock_now каждую секунду. */
export const withOpClock = (snapshot, nowMs) => (
    snapshot ? { ...snapshot, clock_now: opClockNow(snapshot, nowMs) } : snapshot
);

/* Сколько длится статус строки: от начала живого звонка или от события телефона. */
export const opStatusElapsed = (row, snapshot) => {
    const since = wallSeconds(row?.live?.since || row?.status_at);
    const now = snapshot?.clock_now;
    if (since === null || !Number.isFinite(now)) {
        return Number.isFinite(Number(row?.status_seconds)) && row?.status_seconds !== null ? Number(row.status_seconds) : null;
    }
    return Math.max(0, now - since);
};

/* «Ждут дольше всех» — тикает от входа в очередь самого раннего. Очереди нет — null. */
export const opQueueWait = (snapshot) => {
    const now = snapshot?.now;
    if (!now || now.queue === null || now.queue === undefined || !Number(now.queue)) return null;
    const since = wallSeconds(now.queue_since);
    if (since === null || !Number.isFinite(snapshot?.clock_now)) {
        return Number.isFinite(Number(now.queue_max_wait_seconds)) ? Number(now.queue_max_wait_seconds) : null;
    }
    return Math.max(0, snapshot.clock_now - since);
};

/*
 * Плитка «В очереди» — то же правило, что у табло СЗоВ: пусто — хорошо; очередь есть и никто
 * не свободен — тревога; иначе предупреждение. Коннектор не на связи — нейтрально (прочерк).
 */
export const opQueueTone = (now) => {
    if (!now || now.queue === null || now.queue === undefined) return 'neutral';
    if (Number(now.queue) === 0) return 'good';
    return Number(now.operators_free) === 0 ? 'bad' : 'warn';
};
