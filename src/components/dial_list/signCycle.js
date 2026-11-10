/*
 * Круг проверки подписания в «Аналитике» «Удаленного КЦ» — строка «обновлено … · следующее через …»
 * (владелец, 10.11.2026: «отображать ближайшее обновление… обратный отсчёт»). Без React.
 *
 * Сервер по кругу, без перерывов спрашивает Sapar о каждом неподписавшем водителе базы и отдаёт в
 * sign_check ответа аналитики:
 *   updated_at      когда закончился последний полный круг (после перезапуска сервера, пока круг не
 *                   пройден, — пусто; тогда берём last_checked_at — когда последний раз записано
 *                   состояние водителя);
 *   next_update_at  когда закончится следующий — оценка сервера по скорости идущего круга;
 *   updating        идёт ли круг сейчас;
 *   server_now      часы сервера: отсчёт идёт по ним, а не по часам компьютера.
 */

const pad2 = (n) => String(n).padStart(2, '0');

const parse = (iso) => {
    const ms = iso ? Date.parse(iso) : NaN;
    return Number.isNaN(ms) ? null : ms;
};

/* Насколько часы сервера впереди часов этого компьютера, мс. receivedAt — Date.now() в момент ответа. */
export const clockOffset = (check, receivedAt) => {
    const server = parse(check?.server_now);
    return server == null ? 0 : server - receivedAt;
};

/* Сколько миллисекунд до конца круга по часам сервера; null — оценки нет. */
export const msLeft = (check, offsetMs, nowMs) => {
    const next = parse(check?.next_update_at);
    return next == null ? null : next - (nowMs + (offsetMs || 0));
};

/* 462 → «07:42», 3909 → «1:05:09». */
export const fmtCountdown = (seconds) => {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(s / 3600);
    const rest = `${pad2(Math.floor((s % 3600) / 60))}:${pad2(s % 60)}`;
    return h ? `${h}:${rest}` : rest;
};

/*
 * Что написать после «Документы за <месяц>»: { text, countdown }. countdown — «07:42» до конца круга
 * (подпись «следующее через» ставит экран), пусто — отсчёта нет. fmtWhen — «сегодня, 19:48».
 */
export const signCycleLine = (check, { nowMs, offsetMs = 0, fmtWhen }) => {
    if (!check) return { text: '', countdown: '' };
    if (!check.active) return { text: 'эта база больше не проверяется', countdown: '' };
    if (!check.configured) {
        // Sapar не подключён — обновляться нечему; показываем, что знаем.
        return { text: check.last_checked_at ? `проверено ${fmtWhen(check.last_checked_at)}` : '', countdown: '' };
    }
    const updatedAt = check.updated_at || check.last_checked_at;
    if (!updatedAt) return { text: 'первая проверка — в ближайшие минуты', countdown: '' };
    const updated = `обновлено ${fmtWhen(updatedAt)}`;
    const left = msLeft(check, offsetMs, nowMs);
    if (left != null && left > 0) return { text: updated, countdown: fmtCountdown(Math.ceil(left / 1000)) };
    // Оценка вышла, а новых чисел ещё нет (или сервер оценки не дал, но круг идёт) — ждём его.
    if (left != null || check.updating) return { text: `${updated} · идёт обновление…`, countdown: '' };
    return { text: updated, countdown: '' };
};

/*
 * Через сколько миллисекунд самим перезапросить аналитику; null — незачем (старая база, нет Sapar).
 * В конце круга (с запасом на запись последних ответов) числа на вкладке обновляются без кнопки.
 * Оценка сервера могла не сбыться или её нет — тогда он пришлёт новую; чаще RELOAD_MIN_MS не спрашиваем,
 * реже RELOAD_MAX_MS — тоже: статусы меняются и по ходу круга, а отсчёт подравнивается. failed — прошлый
 * такой запрос не удался (сеть, вышла сессия): следующий — только через RELOAD_MAX_MS, а не каждые секунды.
 */
export const RELOAD_GAP_MS = 2000;
export const RELOAD_MIN_MS = 5000;
export const RELOAD_MAX_MS = 5 * 60 * 1000;
export const RELOAD_UNKNOWN_MS = 20000;

export const reloadDelay = (check, offsetMs, nowMs, failed = false) => {
    if (!check || !check.active || !check.configured) return null;
    if (failed) return RELOAD_MAX_MS;
    const left = msLeft(check, offsetMs, nowMs);
    if (left == null) return RELOAD_UNKNOWN_MS;
    return Math.min(RELOAD_MAX_MS, Math.max(RELOAD_MIN_MS, left + RELOAD_GAP_MS));
};
