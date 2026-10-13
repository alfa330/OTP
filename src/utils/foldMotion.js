/* Плавное раскрытие и сворачивание раздела внутри окна или страницы.
 *
 * Раздел «по запросу» (строка с названием, под ней — содержимое) раскрывался
 * разом: содержимое возникало целиком, строки под ним перескакивали вниз, а при
 * сворачивании всё исчезало за один кадр. Просьба владельца 13.10.2026 об окне
 * заявки «Оплаты счетов»: «открытие табов — ход заявки, документы и историю —
 * сделать более плавным, аккуратно и удобно, в стиле ios/macos».
 *
 * Как в списках iOS и macOS: тело раздела растёт по высоте, и всё, что под ним,
 * едет вместе с ним; содержимое проявляется по ходу раскрытия и гаснет по ходу
 * сворачивания. Здесь только расчёт — какими кадрами и сколько времени; замер
 * высоты и запуск движения живут в компоненте (payments/paymentsUi.jsx, Fold).
 * Отдельным файлом без JSX, чтобы правило проверялось тестом без браузера.
 */
import { WINDOW_RESIZE_EASING } from './windowResize.js';

// Меньше — не движение, а дрожь.
export const FOLD_MIN_DELTA = 2;

/* Короткий раздел раскрывается быстро, длинный — дольше, но не тянется: история
   на сорок событий не должна ехать полсекунды. */
export const FOLD_MIN_MS = 240;
export const FOLD_MAX_MS = 400;
const MS_PER_PIXEL = 0.25;

// Сворачивается раздел чуть быстрее, чем раскрывается: уходящее не задерживают.
export const FOLD_CLOSE_RATIO = 0.85;

/* Кривая та же, что у рамки окна (разгон и торможение): раздел растёт, рамка
   окна растёт вместе с ним — у них один ход. Кривая «бросок и долгое
   приземление» на сотнях пикселей даёт рывок в первом же кадре. */
export const FOLD_EASING = WINDOW_RESIZE_EASING;

/* Содержимое проявляется за первые 70 % хода: в конце раскрытия текст уже чёткий.
   Гаснет — почти до самого конца (90 %): если погасить его раньше, раздел
   досворачивается пустой белой полосой — это видно на замедленной съёмке. */
export const FOLD_FADE_IN_END = 0.7;
export const FOLD_FADE_OUT_END = 0.9;

// Сколько от края окна держать раскрывающийся раздел.
export const FOLD_VIEW_MARGIN = 12;

const unit = (value, fallback) => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.min(1, Math.max(0, number)) : fallback;
};

/** Движение тела раздела от высоты `from` к `to` (в пикселях; ноль — свёрнут).
 *  `opacity` — прозрачность, на которой движение начинается: раздел могли
 *  развернуть обратно на полпути. Возвращает { opening, frames, duration,
 *  easing } либо null, когда двигать нечего. */
export const foldMotion = (from, to, opacity) => {
    const start = Number(from);
    const end = Number(to);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end < 0) return null;
    const distance = Math.abs(end - start);
    if (distance < FOLD_MIN_DELTA) return null;
    const opening = end > start;
    const seen = unit(opacity, opening ? 0 : 1);
    const time = Math.min(FOLD_MAX_MS, FOLD_MIN_MS + distance * MS_PER_PIXEL);
    return {
        opening,
        duration: Math.round(opening ? time : time * FOLD_CLOSE_RATIO),
        easing: FOLD_EASING,
        frames: [
            { height: `${start}px`, opacity: seen, offset: 0 },
            { opacity: opening ? 1 : 0, offset: opening ? FOLD_FADE_IN_END : FOLD_FADE_OUT_END },
            { height: `${end}px`, opacity: opening ? 1 : 0, offset: 1 },
        ],
    };
};

/** На сколько пикселей подкрутить окно в этом кадре, чтобы раскрывающийся раздел
 *  оставался на виду: низ раздела ушёл под край — окно едет следом. Но не
 *  дальше, чем название раздела остаётся у верхнего края: длинную историю
 *  читают с начала. Края — координаты на экране. */
export const foldFollowStep = (sectionTop, sectionBottom, viewTop, viewBottom, margin = FOLD_VIEW_MARGIN) => {
    const below = Number(sectionBottom) - (Number(viewBottom) - margin);
    const room = Number(sectionTop) - (Number(viewTop) + margin);
    if (!(below > 0.5) || !(room > 0.5)) return 0;
    return Math.min(below, room);
};
