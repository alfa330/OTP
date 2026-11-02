import { SLEEP_AFTER_MS, lookVector } from './mood.js';

const RECT_TTL_MS = 500;
const DRAG_CLICK_MS = 150;

/* Жесты шарика → события настроения талисмана (тыква, котик). Чистый DOM,
 * без React: порядок событий настоящего шарика стережёт
 * tests/assistant_mascot_gestures.test.mjs.
 *
 * Талисман не заводит своих жестов: открытие, перетаскивание и окно делает
 * кнопка AssistantOrb, а талисман только слушает её (наведение, нажатие, класс
 * aorb-dragging, aria-expanded) — так ни один жест шарика не меняется.
 *
 *   мышью — pointerdown («нажатие») → pointerup переключает помощника →
 *           наблюдатель видит aria-expanded → и только потом click;
 *   с клавиатуры — click (detail 0) приходит ДО переключения: наш слушатель на
 *           кнопке срабатывает раньше делегированного onClick React. Щелчок,
 *           который сейчас закроет помощника, помечаем closing — круг эмоций
 *           он не двигает, как и щелчок мышью после закрытия.
 *
 * Взгляд — мимо React: onLook(x, y) зовётся не чаще кадра и только когда
 * взгляд заметно сдвинулся; положение талисмана читается не чаще раза в
 * полсекунды. Сон — SLEEP_AFTER_MS без движения мыши, клавиш, колеса и
 * нажатий; таймер не перезаводится на каждом движении, а раз в секунду
 * сверяется с последним. «Проснуться» шлём только спящему — иначе каждое
 * движение мыши стоило бы перерисовки.
 *
 * Окно и документ — того узла, где живёт талисман: котик в пустом чате
 * откреплённого окна (PiP) слушает своё окно, а не портал.
 *
 * Возвращает функцию отписки.
 */
export function listenToOrb(root, { dispatch, isSleeping, onLook, now = () => Date.now() }) {
    const doc = root.ownerDocument || document;
    const win = doc.defaultView || window;
    const Observer = win.MutationObserver || globalThis.MutationObserver;
    const target = root.closest('.aorb-button') || root;
    let lastDragEnd = Number.NEGATIVE_INFINITY;
    let dragging = target.classList.contains('aorb-dragging');
    let expanded = target.getAttribute('aria-expanded') === 'true';
    let lastActive = now();

    const active = () => {
        lastActive = now();
        if (isSleeping()) dispatch({ type: 'wake', now: lastActive });
    };

    const onEnter = () => dispatch({ type: 'enter', now: now() });
    const onLeave = () => dispatch({ type: 'leave', now: now() });
    const onDown = () => {
        active();
        dispatch({ type: 'press', now: now() });
    };
    const onClick = (event) => {
        if (now() - lastDragEnd < DRAG_CLICK_MS) return;  // это был конец перетаскивания
        active();
        dispatch({ type: 'click', now: now(), closing: event.detail === 0 && expanded });
    };
    target.addEventListener('pointerenter', onEnter);
    target.addEventListener('pointerleave', onLeave);
    target.addEventListener('pointerdown', onDown);
    target.addEventListener('click', onClick);

    const observer = new Observer(() => {
        const nextDragging = target.classList.contains('aorb-dragging');
        if (nextDragging !== dragging) {
            dragging = nextDragging;
            if (dragging) dispatch({ type: 'dragStart', now: now() });
            else {
                lastDragEnd = now();
                dispatch({ type: 'dragEnd', now: lastDragEnd });
            }
        }
        const nextExpanded = target.getAttribute('aria-expanded') === 'true';
        if (nextExpanded !== expanded) {
            if (expanded && !nextExpanded) dispatch({ type: 'close', now: now() });
            expanded = nextExpanded;
        }
    });
    if (target !== root) observer.observe(target, { attributes: true, attributeFilter: ['class', 'aria-expanded'] });

    let frame = 0;
    let pointer = null;
    let rect = null;
    let rectAt = Number.NEGATIVE_INFINITY;
    let lookX = 0;
    let lookY = 0;
    const setLook = (x, y) => {
        if (Math.abs(x - lookX) < 0.02 && Math.abs(y - lookY) < 0.02) return;
        lookX = x;
        lookY = y;
        onLook?.(x, y);
    };
    const applyLook = () => {
        frame = 0;
        if (!pointer) return;
        const at = now();
        if (!rect || at - rectAt > RECT_TTL_MS || dragging) {
            rect = root.getBoundingClientRect();
            rectAt = at;
        }
        const look = lookVector(pointer.x - (rect.left + rect.width / 2), pointer.y - (rect.top + rect.height / 2));
        setLook(look.x, look.y);
    };
    const idleTimer = setInterval(() => {
        if (!isSleeping() && now() - lastActive >= SLEEP_AFTER_MS) dispatch({ type: 'sleep', now: now() });
    }, 1000);
    idleTimer?.unref?.();
    const onMove = (event) => {
        pointer = { x: event.clientX, y: event.clientY };
        if (!frame) frame = win.requestAnimationFrame(applyLook);
        active();
    };
    const onOut = (event) => {
        if (event.relatedTarget) return;      // ушли не за окно, а на другой элемент
        pointer = null;
        setLook(0, 0);
    };
    win.addEventListener('pointermove', onMove, { passive: true });
    doc.addEventListener('pointerout', onOut);
    win.addEventListener('keydown', active, { passive: true });
    win.addEventListener('wheel', active, { passive: true });

    return () => {
        target.removeEventListener('pointerenter', onEnter);
        target.removeEventListener('pointerleave', onLeave);
        target.removeEventListener('pointerdown', onDown);
        target.removeEventListener('click', onClick);
        observer.disconnect();
        win.removeEventListener('pointermove', onMove);
        doc.removeEventListener('pointerout', onOut);
        win.removeEventListener('keydown', active);
        win.removeEventListener('wheel', active);
        win.cancelAnimationFrame?.(frame);
        clearInterval(idleTimer);
    };
}
