/* Время сообщения в ленте «Чатов ОП».
 *
 * Время с сервера — TIMESTAMPTZ строкой в UTC (`…+00:00`): сессия базы на
 * Render в UTC. День ленты считается по МЕСТНОМУ времени, как и время в пузыре
 * (toLocaleTimeString): срез строки давал день по UTC, и переписка с 00:00 до
 * 05:00 по Алматы уходила под разделитель предыдущей даты.
 *
 * wazzupDt есть у входящего, которое Wazzup доставил с опозданием (канал на
 * обычном WhatsApp был отключён — накопленное приходит со временем отправки):
 * dt у такого сообщения — минута, когда оно дошло до Wazzup, а когда клиент его
 * написал, видно в подсказке (бэкенд: wazzup/delivery.py). */

const pad = (n) => String(n).padStart(2, '0');

const parse = (iso) => {
    if (!iso) return null;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d;
};

/* Порядок ленты — как у сервера: время с точностью до МИКРОСЕКУНДЫ, затем id.
 * Date хранит только миллисекунды, а сервер разводит сообщения и на 1 мкс:
 * ответ клиента «2ГИС» встаёт сразу за ответом оператора из окна iCORE
 * (wazzup/delivery.py, «Ответы из окна iCORE»), опоздавшие сжаты в первую
 * миллисекунду минуты. По Date такие сообщения равны, и порядок решал бы
 * случайный messageId. Ключ меньше 2^53 (≈1,8·10^15). */
export const messageTimeKey = (iso) => {
    const ms = Date.parse(iso);
    if (!Number.isFinite(ms)) return NaN;
    const fraction = /\.\d{3}(\d{1,3})/.exec(String(iso));
    return ms * 1000 + (fraction ? Number(fraction[1].padEnd(3, '0')) : 0);
};

export const compareMessageTime = (a, b) => {
    const delta = messageTimeKey(a.dt) - messageTimeKey(b.dt);
    return (Number.isFinite(delta) ? delta : 0) || String(a.messageId).localeCompare(String(b.messageId));
};

export const localDayKey = (iso) => {
    const d = parse(iso);
    return d ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` : '';
};

const clock = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

export const lateDeliveryNote = (msg) => {
    if (!msg || msg.isEcho) return '';
    const sent = parse(msg.wazzupDt);
    if (!sent) return '';
    const shown = parse(msg.dt);
    const sameDay = shown && localDayKey(msg.wazzupDt) === localDayKey(msg.dt);
    const day = shown && sent.getFullYear() !== shown.getFullYear()
        ? `${pad(sent.getDate())}.${pad(sent.getMonth() + 1)}.${sent.getFullYear()}`
        : `${pad(sent.getDate())}.${pad(sent.getMonth() + 1)}`;
    const when = sameDay ? `в ${clock(sent)}` : `${day} в ${clock(sent)}`;
    return `Клиент отправил ${when}, в Wazzup сообщение пришло позже`;
};
