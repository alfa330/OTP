/* Хэллоуин-2026: сроки временного оформления портала.
 *
 * Решение владельца 30.10.2026: украшения висят до конца 1 ноября и исчезают
 * сами — «после 1 ноября 23:59». Граница — абсолютный момент по Казахстану
 * (UTC+5, вся страна с марта 2024): у человека с другим поясом в системе слой
 * гаснет в ту же секунду, что и у всех.
 *
 * Этот модуль main.jsx импортирует сразу, поэтому в нём только сроки — сам
 * слой (картинки, стили, раскладка) едет отдельным чанком и после срока не
 * грузится вовсе.
 */

export const HALLOWEEN_ENDS_AT = Date.parse('2026-11-02T00:00:00+05:00');

export const isHalloweenTime = (now = Date.now()) => Number.isFinite(now) && now < HALLOWEEN_ENDS_AT;

/* Окно «Чатов ОП» в CRM (?embed=1) и любой чужой фрейм — не наш экран:
   там украшения не рисуются. */
export const shouldMountHalloween = ({ now = Date.now(), search = '', framed = false } = {}) => {
    if (!isHalloweenTime(now) || framed) return false;
    try {
        return new URLSearchParams(String(search || '')).get('embed') !== '1';
    } catch (error) {
        return true;
    }
};
