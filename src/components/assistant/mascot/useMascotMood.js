import { useEffect, useReducer, useRef, useState } from 'react';
import { SLEEP_AFTER_MS, lookVector } from './mood.js';

const RECT_TTL_MS = 500;

/* Настроение талисмана в шарике (тыква, котик) — общий для них слух.
 *
 * Талисман не заводит своих жестов: открытие, перетаскивание и окно делает
 * кнопка AssistantOrb, а талисман только слушает её (наведение, нажатие, класс
 * aorb-dragging, aria-expanded) — так ни один жест шарика не меняется.
 *
 * Взгляд — мимо React: onLook(x, y) зовётся не чаще кадра и только когда
 * взгляд заметно сдвинулся, положение талисмана читается не чаще раза в
 * полсекунды. Сон — SLEEP_AFTER_MS без движения мыши и клавиш; таймер не
 * перезаводится на каждом движении, а раз в секунду сверяется с последним.
 *
 * Возвращает эмоцию, которую показать сейчас.
 */
export default function useMascotMood(rootRef, mood, { interactive = true, onLook } = {}) {
    const [state, dispatch] = useReducer(mood.moodReducer, undefined, mood.initialMood);
    const [, setTick] = useState(0);
    const lookRef = useRef(onLook);
    lookRef.current = onLook;
    /* Бодрствующего талисмана движение мыши не трогает вовсе: «проснуться» шлём
       только спящему. Иначе каждое движение — лишняя перерисовка компонента
       (useReducer не отбрасывает действие до рендера, даже если состояние то же). */
    const sleepingRef = useRef(state.sleeping);
    sleepingRef.current = state.sleeping;

    /* Эмоция меняется сама (гаснет временная, дремота переходит в сон) —
       перерисовка ровно в срок. */
    useEffect(() => {
        const at = mood.nextChangeAt(state, Date.now());
        if (at == null) return undefined;
        const timer = window.setTimeout(() => setTick((n) => n + 1), Math.max(0, at - Date.now()) + 16);
        return () => window.clearTimeout(timer);
    }, [mood, state]);

    useEffect(() => {
        const root = rootRef.current;
        if (!root || !interactive) return undefined;
        const target = root.closest('.aorb-button') || root;
        const now = () => Date.now();
        let lastDragEnd = 0;
        let dragging = target.classList.contains('aorb-dragging');
        let expanded = target.getAttribute('aria-expanded') === 'true';

        const onEnter = () => dispatch({ type: 'enter', now: now() });
        const onLeave = () => dispatch({ type: 'leave', now: now() });
        const onDown = () => dispatch({ type: 'press', now: now() });
        const onClick = () => {
            if (now() - lastDragEnd < 150) return;  // это был конец перетаскивания
            dispatch({ type: 'click', now: now() });
        };
        target.addEventListener('pointerenter', onEnter);
        target.addEventListener('pointerleave', onLeave);
        target.addEventListener('pointerdown', onDown);
        target.addEventListener('click', onClick);

        const observer = new MutationObserver(() => {
            const nextDragging = target.classList.contains('aorb-dragging');
            if (nextDragging !== dragging) {
                dragging = nextDragging;
                if (dragging) dispatch({ type: 'dragStart', now: now() });
                else {
                    lastDragEnd = now();
                    dispatch({ type: 'dragEnd', now: now() });
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
        let rectAt = 0;
        let lookX = 0;
        let lookY = 0;
        const setLook = (x, y) => {
            if (Math.abs(x - lookX) < 0.02 && Math.abs(y - lookY) < 0.02) return;
            lookX = x;
            lookY = y;
            lookRef.current?.(x, y);
        };
        const applyLook = () => {
            frame = 0;
            if (!pointer) return;
            const at = Date.now();
            if (!rect || at - rectAt > RECT_TTL_MS || dragging) {
                rect = root.getBoundingClientRect();
                rectAt = at;
            }
            const look = lookVector(pointer.x - (rect.left + rect.width / 2), pointer.y - (rect.top + rect.height / 2));
            setLook(look.x, look.y);
        };
        let lastActive = now();
        const idleTimer = setInterval(() => {
            if (now() - lastActive >= SLEEP_AFTER_MS) dispatch({ type: 'sleep', now: now() });
        }, 1000);
        idleTimer?.unref?.();
        const onMove = (event) => {
            pointer = { x: event.clientX, y: event.clientY };
            if (!frame) frame = window.requestAnimationFrame(applyLook);
            lastActive = now();
            if (sleepingRef.current) dispatch({ type: 'wake', now: lastActive });
        };
        const onOut = (event) => {
            if (event.relatedTarget) return;      // ушли не за окно, а на другой элемент
            pointer = null;
            setLook(0, 0);
        };
        const onKey = () => {
            lastActive = now();
            if (sleepingRef.current) dispatch({ type: 'wake', now: lastActive });
        };
        window.addEventListener('pointermove', onMove, { passive: true });
        document.addEventListener('pointerout', onOut);
        window.addEventListener('keydown', onKey, { passive: true });

        return () => {
            target.removeEventListener('pointerenter', onEnter);
            target.removeEventListener('pointerleave', onLeave);
            target.removeEventListener('pointerdown', onDown);
            target.removeEventListener('click', onClick);
            observer.disconnect();
            window.removeEventListener('pointermove', onMove);
            document.removeEventListener('pointerout', onOut);
            window.removeEventListener('keydown', onKey);
            window.cancelAnimationFrame?.(frame);
            clearInterval(idleTimer);
        };
    }, [rootRef, interactive]);

    return mood.displayEmotion(state, Date.now());
}
