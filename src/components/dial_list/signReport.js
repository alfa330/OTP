/*
 * Отчёт «Подписание водителей по городам» в Telegram-группы (владелец, 19.10.2026) — правила
 * окна выбора групп без React: изменился ли выбор, что сказать о группах и о последней отправке.
 * Проверяются node-тестом (tests/dial_list_sign_report.test.mjs).
 */

const pad2 = (n) => String(n).padStart(2, '0');

/* Тот же набор групп, порядок не важен. */
export const sameChatIds = (a = [], b = []) => {
    const left = [...new Set((a || []).map(String))].sort();
    const right = [...new Set((b || []).map(String))].sort();
    return left.length === right.length && left.every((value, index) => value === right[index]);
};

/* Когда отчёт дошёл: «сегодня, 10:00» / «вчера, 19:00» / «17.10, 10:00». */
export const fmtSentAt = (iso, now = new Date()) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const clock = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    const sameDay = (x, y) => x.getFullYear() === y.getFullYear() && x.getMonth() === y.getMonth()
        && x.getDate() === y.getDate();
    if (sameDay(d, now)) return `сегодня, ${clock}`;
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (sameDay(d, yesterday)) return `вчера, ${clock}`;
    return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}, ${clock}`;
};

/* Что с выбранными группами не так — строками: бота убрали из группы (отчёт туда не дойдёт)
   и последний отчёт не дошёл (текст Telegram). Всё в порядке — пусто: шуметь нечем. */
export const reportNotices = (selected = []) => {
    const notices = [];
    const lost = (selected || []).filter((s) => s.is_available === false);
    if (lost.length) {
        const names = lost.map((s) => `«${s.title}»`).join(', ');
        notices.push({
            tone: 'amber',
            text: `Бота больше нет в ${lost.length === 1 ? 'группе' : 'группах'} ${names} — отчёт туда не дойдёт.`,
        });
    }
    (selected || []).filter((s) => s.is_available !== false && s.last_error).forEach((s) => {
        notices.push({ tone: 'rose', text: `«${s.title}»: последний отчёт не дошёл — ${s.last_error}` });
    });
    return notices;
};

/* Строка о последней дошедшей отправке — по самой свежей из выбранных групп. */
export const lastSentLine = (selected = [], now = new Date()) => {
    const times = (selected || []).map((s) => s.last_sent_at).filter(Boolean)
        .map((iso) => new Date(iso)).filter((d) => !Number.isNaN(d.getTime()))
        .sort((x, y) => x - y);
    if (!times.length) return '';
    return `Последний отчёт ушёл ${fmtSentAt(times[times.length - 1].toISOString(), now)}`;
};

/* «в 10:00 и 19:00» — из расписания сервера. */
export const scheduleText = (schedule = []) => {
    const list = (schedule || []).filter(Boolean);
    if (!list.length) return '';
    if (list.length === 1) return `в ${list[0]}`;
    return `в ${list.slice(0, -1).join(', ')} и ${list[list.length - 1]}`;
};
