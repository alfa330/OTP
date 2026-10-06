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
