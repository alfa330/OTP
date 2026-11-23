/* Прокрутка к цели урока — только внутри документа учебной CRM.
 *
 * ЗАЧЕМ НЕ scrollIntoView. Он прокручивает ВСЕ блоки до самого верха, и
 * снаружи фрейма тоже: обёртку фрейма (overflow: hidden прокрутке не
 * мешает) — CRM уезжала вверх, шапка окна «Заказ …» пряталась, а снизу
 * открывалась серая полоса; и страницу вики, в которой открыт тренажёр.
 * С плавной прокруткой внешний блок к тому же считает от ещё не доехавшего
 * внутреннего и уезжает лишнее. Поэтому — сами: от цели вверх по
 * прокручиваемым блокам документа CRM, «nearest», как scrollIntoView.
 *
 * Модуль без React: его гоняют тесты обычным node на макете документа.
 */

/** Насколько сдвинуть отрезок [start, end] внутрь [min, max]: 0 — уже
 * виден; выше края — к верху; ниже — к низу, но не дальше начала цели. */
export const nearestShift = (start, end, min, max) => {
    if (start < min) return start - min;
    if (end > max) return Math.min(end - max, start - min);
    return 0;
};

const SCROLLS = /(auto|scroll|overlay)/;

/**
 * Показать узел node документа doc, прокручивая только блоки внутри doc.
 * Шапку CRM (.app-header, она закреплена сверху) считает краем: цель под
 * шапкой — тоже не видна.
 */
export const revealInFrame = (doc, node, behavior = 'smooth') => {
    const win = doc && doc.defaultView;
    if (!win || !node || !node.getClientRects().length) return;
    /* Блок, который уже решено прокрутить, ещё не доехал: внешним блокам
       цель видна там, где окажется, — со сдвигом. */
    let shiftX = 0;
    let shiftY = 0;
    for (let box = node.parentElement; box && box !== doc.body && box !== doc.documentElement; box = box.parentElement) {
        const style = win.getComputedStyle(box);
        const canY = SCROLLS.test(style.overflowY) && box.scrollHeight > box.clientHeight;
        const canX = SCROLLS.test(style.overflowX) && box.scrollWidth > box.clientWidth;
        if (canX || canY) {
            const outer = box.getBoundingClientRect();
            const inner = node.getBoundingClientRect();
            const top = outer.top + box.clientTop;
            const left = outer.left + box.clientLeft;
            const dy = canY ? nearestShift(inner.top - shiftY, inner.bottom - shiftY, top, top + box.clientHeight) : 0;
            const dx = canX ? nearestShift(inner.left - shiftX, inner.right - shiftX, left, left + box.clientWidth) : 0;
            if (dx || dy) box.scrollBy({ left: dx, top: dy, behavior });
            shiftX += dx;
            shiftY += dy;
        }
        /* Закреплённый блок (окно поверх CRM, меню слева) прокруткой
           страницы не двигается — дальше вверх крутить нечего. */
        if (style.position === 'fixed') return;
    }
    const header = doc.querySelector('.app-header');
    const ceiling = header ? Math.max(0, header.getBoundingClientRect().bottom) : 0;
    const inner = node.getBoundingClientRect();
    const dy = nearestShift(inner.top - shiftY, inner.bottom - shiftY, ceiling, win.innerHeight);
    const dx = nearestShift(inner.left - shiftX, inner.right - shiftX, 0, win.innerWidth);
    if (dx || dy) win.scrollBy({ left: dx, top: dy, behavior });
};
