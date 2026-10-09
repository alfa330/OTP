/* Чистые функции рабочего места верификатора в «Чатах ОП».
 *
 * Отдельным модулем, как chatLink.js: их гоняет node-тест без браузера и без
 * React. Справочник статусов сюда НЕ копируется — он приходит с сервера
 * (wazzup/shift.py -> STATUSES), и вторая копия разошлась бы с первой. */

/* Цвет точки статуса — по смыслу, а не по названию: работа зелёная, перерыв
   янтарный, тренинг синий, техническая пауза фиолетовая. Незнакомый тон — серый. */
export const STATUS_DOT_CLASS = {
    work: 'bg-emerald-500',
    break: 'bg-amber-500',
    training: 'bg-sky-500',
    tech: 'bg-violet-500',
    other: 'bg-slate-400',
    off: 'bg-slate-300',
};

export const statusDotClass = (tone) => STATUS_DOT_CLASS[tone] || STATUS_DOT_CLASS.other;

/* Счётчик времени на кнопке нужен только паузам: сколько идёт перерыв, человек
   следит сам, а «Активный · 3 ч 12 мин» был бы числом, на которое никто не
   смотрит. */
export const statusShowsTimer = (tone) => tone === 'break' || tone === 'training' || tone === 'tech';

/* null — «неизвестно», а не ноль: Number(null) дал бы 0 и «меньше минуты». */
const toNumberOrNull = (value) => (
    value === null || value === undefined || value === '' ? null : Number(value)
);

/* «1 ч 24 мин», «12 мин», «меньше минуты». Секунды не показываем: подпись
   обновляется раз в полминуты, и бегущие секунды в шапке — лишнее движение. */
export const formatShiftElapsed = (seconds) => {
    const value = toNumberOrNull(seconds);
    if (value === null) return '';
    if (!Number.isFinite(value) || value < 0) return '';
    const minutes = Math.floor(value / 60);
    if (minutes < 1) return 'меньше минуты';
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    if (!hours) return `${rest} мин`;
    return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
};

/* Сколько человек в статусе сейчас: сервер отдаёт прошедшие секунды на момент
   ответа, дальше досчитываем сами. Часы браузера при этом не участвуют — у
   оператора они могут отставать, а момент статуса считал сервер. */
export const elapsedNow = (current, receivedAtMs, nowMs) => {
    const base = toNumberOrNull(current?.elapsedSeconds);
    if (base === null || !Number.isFinite(base)) return null;
    const drift = Math.max(0, Math.floor((Number(nowMs) - Number(receivedAtMs)) / 1000));
    return base + (Number.isFinite(drift) ? drift : 0);
};

/* «ЧЧ:ММ» из naive-ISO сервера (время Алматы) — без new Date(): браузер
   прочитал бы строку в своём поясе и сдвинул бы время. */
export const clockOf = (iso) => {
    const match = /T(\d{2}):(\d{2})/.exec(String(iso || ''));
    return match ? `${match[1]}:${match[2]}` : '';
};

/* Подпись под «Начать смену», когда прошлую смену закрыл сторож: человек должен
   узнать, что смена закрылась не по его нажатию и когда именно. */
export const autoCloseNote = (current) => {
    if (!current || current.onShift || !current.auto) return '';
    const clock = clockOf(current.since);
    return clock ? `Прошлая смена закрыта автоматически в ${clock}` : 'Прошлая смена закрыта автоматически';
};

/* id нажатия: повтор того же запроса после обрыва сети не запишет статус
   дважды (сервер вставляет событие идемпотентно по этому id). */
export const newClientEventId = () => {
    const cryptoApi = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
    if (cryptoApi?.randomUUID) return cryptoApi.randomUUID().replace(/-/g, '');
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
};

/* Код из QR — прежний код чатов? Знак ставит сервер (wazzup/access.py: QR_PREFIX).
   По нему сканер выбирает, какой ручкой его разбирать: у такого кода
   подтверждение идёт с кодом из Telegram, у обычного — одним нажатием. С
   09.10.2026 экран чатов показывает обычный код; этот знак — у показанных раньше. */
export const CHAT_ACCESS_QR_PREFIX = 'OTPW:';

export const isChatAccessQr = (raw) => (
    String(raw || '').trim().toUpperCase().startsWith(CHAT_ACCESS_QR_PREFIX)
);

/* Что набрано в поле кода: только цифры, не длиннее самого кода. Код диктуют
   парами и вставляют из Telegram с пробелом — лишнее снимаем сами. */
export const CHAT_ACCESS_CODE_DIGITS = 6;

export const cleanAccessCode = (raw) => (
    String(raw || '').replace(/\D/g, '').slice(0, CHAT_ACCESS_CODE_DIGITS)
);

/* «0:45» — сколько ждать до повторной отправки кода. */
export const formatCountdown = (seconds) => {
    const value = Math.max(0, Math.ceil(Number(seconds) || 0));
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
};
