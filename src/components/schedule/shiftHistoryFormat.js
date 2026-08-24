/*
 * Подписи для истории изменений графика.
 *
 * Модуль намеренно чистый (без React) — так формулировки проверяются
 * `node --test`, а не глазами на проде. Смысл записи складывается из пары
 * «действие + источник»: одно и то же «смена появилась» читается по-разному,
 * когда её взяли с аукциона и когда её поставил супервайзер. Поэтому сервер
 * хранит эти две оси раздельно, а собираются они здесь.
 */

export const SHIFT_HISTORY_ACTIONS = Object.freeze({
    ADDED: 'added',
    REMOVED: 'removed',
    CHANGED: 'changed',
    DAY_OFF_SET: 'day_off_set',
    DAY_OFF_CLEARED: 'day_off_cleared',
});

export const SHIFT_HISTORY_SOURCES = Object.freeze({
    SUPERVISOR: 'supervisor',
    AUCTION: 'auction',
    AUCTION_TOPUP: 'auction_topup',
    AUCTION_TOPUP_CANCEL: 'auction_topup_cancel',
    AUCTION_ADMIN: 'auction_admin',
    SWAP: 'swap',
    IMPORT: 'import',
    STATUS_PERIOD: 'status_period',
    SYSTEM: 'system',
});

// Роль автора нужна одним словом перед фамилией — «СВ Иванов», а не «Иванов».
const ROLE_PREFIXES = Object.freeze({
    super_admin: 'Администратор',
    admin: 'Администратор',
    sv: 'Супервайзер',
    trainer: 'Тренер',
    operator: 'Оператор',
    trainee: 'Стажёр',
});

const ACTION_LABELS = Object.freeze({
    added: 'Смена добавлена',
    removed: 'Смена удалена',
    changed: 'Смена изменена',
    day_off_set: 'Проставлен выходной',
    day_off_cleared: 'Выходной снят',
});

// Ключ — «действие·источник». Заполнены только те сочетания, где источник
// меняет смысл фразы; для остальных берётся ACTION_LABELS + автор отдельно.
const ACTION_SOURCE_LABELS = Object.freeze({
    'added·auction': 'Взята с аукциона',
    'added·auction_topup': 'Добор с аукциона',
    'added·auction_admin': 'Выдана с аукциона',
    'added·swap': 'Получена при обмене',
    'added·import': 'Загружена из файла',
    'removed·auction': 'Снята публикацией аукциона',
    'removed·auction_topup_cancel': 'Добор отменён',
    'removed·auction_admin': 'Снята с аукциона',
    'removed·swap': 'Отдана при обмене',
    'removed·import': 'Убрана загрузкой из файла',
    'removed·status_period': 'Снята статусом',
    'changed·auction': 'Пересобрана публикацией аукциона',
    'changed·auction_topup': 'Расширена добором',
    'changed·auction_topup_cancel': 'Урезана отменой добора',
    'changed·swap': 'Пересобрана обменом',
    'changed·import': 'Заменена загрузкой из файла',
    'day_off_set·auction': 'Выходной с аукциона',
    'day_off_set·import': 'Выходной из файла',
    'day_off_set·swap': 'Выходной после обмена',
});

// Источники, где действующее лицо — не автор правки, а сама механика.
// Для них ФИО в подписи не показываем: «Взята с аукциона» самодостаточно,
// а приписка «оператор Иванов» дублировала бы имя строки графика.
const IMPERSONAL_SOURCES = new Set([
    SHIFT_HISTORY_SOURCES.AUCTION,
    SHIFT_HISTORY_SOURCES.AUCTION_TOPUP,
    SHIFT_HISTORY_SOURCES.AUCTION_TOPUP_CANCEL,
]);

export const shiftHistoryActionLabel = (entry) => {
    const action = String(entry?.action || '').trim();
    const source = String(entry?.source || '').trim();
    return ACTION_SOURCE_LABELS[`${action}·${source}`] || ACTION_LABELS[action] || action || '—';
};

/** Кто сделал: «Супервайзер Иванов И.». Пусто, когда автора нет или он не важен. */
export const shiftHistoryActorLabel = (entry) => {
    const source = String(entry?.source || '').trim();
    if (IMPERSONAL_SOURCES.has(source)) return '';
    const name = String(entry?.actorName || '').trim();
    if (!name) return '';
    const prefix = ROLE_PREFIXES[String(entry?.actorRole || '').trim()];
    return prefix ? `${prefix} ${name}` : name;
};

/** Времена смены: «09:00 — 17:00», для правки — «09:00 — 17:00 → 10:00 — 18:00». */
export const shiftHistoryTimeLabel = (entry) => {
    const next = entry?.start && entry?.end ? `${entry.start} — ${entry.end}` : '';
    const prev = entry?.prevStart && entry?.prevEnd ? `${entry.prevStart} — ${entry.prevEnd}` : '';
    if (prev && next && prev !== next) return `${prev} → ${next}`;
    return next || prev || '';
};

const TONES = Object.freeze({
    added: 'green',
    removed: 'red',
    changed: 'blue',
    day_off_set: 'sky',
    day_off_cleared: 'slate',
});

/** Цвет несёт только смысл действия; нейтральное состояние остаётся серым. */
export const shiftHistoryTone = (entry) => TONES[String(entry?.action || '').trim()] || 'slate';

const pad2 = (value) => String(value).padStart(2, '0');

const toDate = (iso) => {
    if (!iso) return null;
    const parsed = new Date(iso);
    // new Date(null) — это 1970, а не «даты нет»: проверяем оба случая.
    return Number.isNaN(parsed.getTime()) ? null : parsed;
};

/** «24.08, 10:53» — год добавляем только когда он не текущий. */
export const shiftHistoryWhenLabel = (iso, now = new Date()) => {
    const parsed = toDate(iso);
    if (!parsed) return '';
    const day = `${pad2(parsed.getDate())}.${pad2(parsed.getMonth() + 1)}`;
    const year = parsed.getFullYear() === now.getFullYear() ? '' : `.${parsed.getFullYear()}`;
    return `${day}${year}, ${pad2(parsed.getHours())}:${pad2(parsed.getMinutes())}`;
};

/** Полная подпись для подсказки: «Смена изменена · Супервайзер Иванов · 24.08, 10:53». */
export const shiftHistoryTooltipLine = (summary, now = new Date()) => {
    if (!summary) return '';
    const entry = {
        action: summary.lastAction,
        source: summary.lastSource,
        actorName: summary.lastActorName,
        actorRole: summary.lastActorRole,
    };
    const parts = [shiftHistoryActionLabel(entry)];
    const actor = shiftHistoryActorLabel(entry);
    if (actor) parts.push(actor);
    const when = shiftHistoryWhenLabel(summary.lastAt, now);
    if (when) parts.push(when);
    const count = Number(summary.count || 0);
    const tail = count > 1 ? ` · всего правок: ${count}` : '';
    return `${parts.join(' · ')}${tail}`;
};

/** Ключ ячейки — тот же формат, что у выделения в сетке: `${opId}|${date}`. */
export const shiftHistoryCellKey = (operatorId, date) => `${operatorId}|${date}`;
