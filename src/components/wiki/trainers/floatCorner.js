/* Где парить Барсу на рабочем месте оператора.
 *
 * Владелец, 23.11.2026: Барс — не колонка слева, а помощник, который парит
 * поверх окна и говорит облачком, что делать. Облачко не должно закрывать то,
 * что сейчас надо нажать: если цель шага оказалась под ним, Барс перелетает в
 * другой угол окна. Углы — по порядку предпочтения: внизу справа (формы и
 * карточки CRM прижаты влево, справа внизу обычно пусто), внизу слева — но
 * ПРАВЕЕ меню CRM, вверху справа (под адресной строкой). Меню CRM Барс не
 * закрывает никогда: на аттестации и в «Звонке» целей не подсвечивают, и
 * уходить ему не от чего, а меню нужно всё время. Дом — внизу справа: как
 * только он свободен, Барс возвращается туда, и место помощника остаётся
 * предсказуемым. Все углы заняты — Барс остаётся, где был, а не мечется.
 *
 * Модуль без React: геометрию гоняют тесты обычным node, а измерение DOM
 * (measureFloat) принимает документ и узлы снаружи.
 */

export const CORNERS = ['br', 'bl', 'tr'];
export const FLOAT_MARGIN = 18;
/* Запас вокруг цели: облачко вплотную к кнопке тоже мешает в неё попасть. */
const PAD = 10;

/** Прямоугольник помощника размера size в углу corner сцены stage.
 *  topInset — отступ верхнего угла (под панелью браузера), leftInset —
 *  левого (правее меню CRM). */
export const cornerRect = (corner, stage, size, topInset = 96, leftInset = 0) => {
    const { width, height } = size;
    const left = corner === 'bl' ? stage.left + leftInset + FLOAT_MARGIN : stage.right - FLOAT_MARGIN - width;
    const top = corner === 'tr' ? stage.top + topInset : stage.bottom - FLOAT_MARGIN - height;
    return { left, top, right: left + width, bottom: top + height };
};

export const overlaps = (a, b, pad = PAD) => Boolean(a && b)
    && a.left < b.right + pad && a.right > b.left - pad
    && a.top < b.bottom + pad && a.bottom > b.top - pad;

/**
 * Угол для помощника: первый свободный по порядку предпочтения (дом —
 * внизу справа). Цели нет — дома; все углы заняты — остаёмся на месте.
 */
export const chooseCorner = ({
    stage, size, target, current = CORNERS[0], topInset = 96, leftInset = 0,
}) => {
    if (!stage || !size) return current;
    if (!target) return CORNERS[0];
    const free = (corner) => !overlaps(cornerRect(corner, stage, size, topInset, leftInset), target);
    return CORNERS.find(free) || current;
};

/**
 * Замер для chooseCorner на живой странице: сцена, размер помощника, цель
 * шага (в окне тренажёра или внутри фрейма CRM — с учётом масштаба фрейма),
 * отступ сверху для верхнего угла — под панелью браузера — и слева — правее
 * меню CRM.
 */
export const measureFloat = (stageEl, floatEl) => {
    if (!stageEl || !floatEl) return null;
    const stage = stageEl.getBoundingClientRect();
    const box = floatEl.getBoundingClientRect();
    const page = stageEl.querySelector('.wt-win__page');
    const topInset = page ? page.getBoundingClientRect().top - stage.top + FLOAT_MARGIN : 96;

    let target = null;
    let leftInset = 0;
    const frame = stageEl.querySelector('.wt-crmframe iframe');
    const doc = frame && frame.contentDocument;
    const outer = frame ? frame.getBoundingClientRect() : null;
    const scale = frame && frame.clientWidth ? outer.width / frame.clientWidth : 1;
    const menu = doc && doc.querySelector('.sidebar');
    if (menu) leftInset = Math.max(0, outer.left + menu.getBoundingClientRect().right * scale - stage.left);
    const inner = doc && doc.querySelector('.is-target');
    if (inner) {
        const r = inner.getBoundingClientRect();
        target = {
            left: outer.left + r.left * scale,
            top: outer.top + r.top * scale,
            right: outer.left + r.right * scale,
            bottom: outer.top + r.bottom * scale,
        };
    } else {
        const own = [...stageEl.querySelectorAll('.is-target')].find((node) => !floatEl.contains(node));
        target = own ? own.getBoundingClientRect() : null;
    }
    return { stage, size: { width: box.width, height: box.height }, target, topInset, leftInset };
};
