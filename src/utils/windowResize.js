/* Плавная смена высоты окна, когда его содержимое подменили разом.
 *
 * Окно портала (IosModal) растёт по содержимому. Если содержимое меняется за
 * один кадр — догрузилась запись, открылся второй шаг формы, — рамка окна
 * перескакивает на новую высоту. Жалоба владельца 12.10.2026 на карточку заявки
 * в «Оплате счетов»: «мини карточка загрузка и резко она увеличивается».
 *
 * Здесь только расчёт — стоит ли двигать рамку, какими кадрами и сколько
 * времени; замер высоты и запуск движения живут в ui/ios.jsx. Отдельным файлом
 * без JSX, чтобы правило проверялось тестом без браузера.
 */

// Пару пикселей глаз не замечает, а движение ради них — лишняя работа.
export const WINDOW_RESIZE_MIN_DELTA = 4;

/* Короткая дорога — быстро, длинная — дольше, но не дольше, чем окно
   открывается (window-in в tailwind.config.cjs — 0,32 с). */
export const WINDOW_RESIZE_MIN_MS = 200;
export const WINDOW_RESIZE_MAX_MS = 320;
const MS_PER_PIXEL = 0.2;

/* Рамка едет ровно: разгон и торможение. Кривая появления окна (бросок и долгое
   приземление) хороша на 12 px подъёма, а на сотнях пикселей роста первый же
   кадр проходил треть пути — тот же рывок, только короче. */
export const WINDOW_RESIZE_EASING = 'cubic-bezier(0.4, 0, 0.2, 1)';

/** Движение рамки от высоты `from` к `to`: { frames, duration, easing } либо
 *  null, когда двигать незачем или не из чего. */
export const windowResizeMotion = (from, to) => {
    const start = Number(from);
    const end = Number(to);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start <= 0 || end <= 0) return null;
    const distance = Math.abs(end - start);
    if (distance < WINDOW_RESIZE_MIN_DELTA) return null;
    return {
        frames: [{ height: `${start}px` }, { height: `${end}px` }],
        duration: Math.round(Math.min(WINDOW_RESIZE_MAX_MS, WINDOW_RESIZE_MIN_MS + distance * MS_PER_PIXEL)),
        easing: WINDOW_RESIZE_EASING,
    };
};
