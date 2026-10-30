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
   там нет ни украшений, ни праздничных сайдбара и входа. */
export const shouldMountHalloween = ({ now = Date.now(), search = '', framed = false } = {}) => {
    if (!isHalloweenTime(now) || framed) return false;
    try {
        return new URLSearchParams(String(search || '')).get('embed') !== '1';
    } catch (error) {
        return true;
    }
};

export const isFramedWindow = () => {
    try {
        return window.self !== window.top;
    } catch (error) {
        return true;
    }
};

const END_POLL_MS = 60 * 1000;

/* Конец праздника в открытой вкладке. Один таймер ровно до полуночи мало:
   таймеры спят вместе с ноутбуком и просыпаются с опозданием на весь сон,
   поэтому ещё проверка раз в минуту и при возвращении на вкладку и в окно.
   onEnd зовётся один раз (сразу, если срок уже прошёл); вернёт отписку.
   Таймеры глобальные и с unref там, где он есть (node в тестах): долгий
   таймер не держит процесс. */
export const watchHalloweenEnd = (onEnd) => {
    if (!isHalloweenTime()) {
        onEnd();
        return () => {};
    }
    const targets = [
        [globalThis.window, 'focus'],
        [globalThis.window, 'pageshow'],
        [globalThis.document, 'visibilitychange'],
    ];
    let timer = null;
    let poll = null;
    let done = false;
    const stop = () => {
        done = true;
        clearTimeout(timer);
        clearInterval(poll);
        targets.forEach(([target, type]) => target?.removeEventListener?.(type, check));
    };
    const check = () => {
        if (done || isHalloweenTime()) return;
        stop();
        onEnd();
    };
    timer = setTimeout(check, Math.max(0, Math.min(HALLOWEEN_ENDS_AT - Date.now() + 50, 2 ** 31 - 1)));
    timer?.unref?.();
    poll = setInterval(check, END_POLL_MS);
    poll?.unref?.();
    targets.forEach(([target, type]) => target?.addEventListener?.(type, check));
    return stop;
};
