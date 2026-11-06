import React, { useLayoutEffect, useRef, useState } from 'react';
import { prefersReducedMotion } from '../ui/ios';
import { foldMotion } from '../../utils/foldMotion';

/*
 * Блок, который раскрывается и сворачивается по высоте — как разделы окон
 * «Оплаты счетов» (Fold в payments/paymentsUi.jsx, расчёт кадров —
 * utils/foldMotion.js), только без строки-заголовка: здесь блок раскрывает
 * выбор плитки в окне «Сформировать отчёт».
 *
 * Без этого отборы табеля возникали бы разом, и окно прыгало бы в новый рост
 * за один кадр. Тело остаётся в разметке, пока сворачивается, перехваченное
 * движение продолжается с того роста, на котором его застали. При «Уменьшить
 * движение» в системе — сразу, без анимации.
 */
export default function HoursFold({ open, children }) {
    const bodyRef = useRef(null);
    const [kept, setKept] = useState(open);
    if (open && !kept) setKept(true);
    const seenOpen = useRef(open);
    const running = useRef(null);

    useLayoutEffect(() => {
        if (seenOpen.current === open) return;
        seenOpen.current = open;
        const body = bodyRef.current;
        if (!body) {
            if (!open) setKept(false);
            return;
        }
        const previous = running.current;
        const startHeight = previous || !open ? body.getBoundingClientRect().height : 0;
        const startOpacity = previous ? Number(getComputedStyle(body).opacity) : undefined;
        if (previous) {
            running.current = null;
            previous.cancel();
        }
        const motion = prefersReducedMotion() || typeof body.animate !== 'function'
            ? null
            : foldMotion(startHeight, open ? body.offsetHeight : 0, startOpacity);
        if (!motion) {
            body.style.overflow = '';
            if (!open) setKept(false);
            return;
        }
        body.style.overflow = 'hidden';
        const animation = body.animate(motion.frames, {
            duration: motion.duration,
            easing: motion.easing,
            fill: open ? 'none' : 'forwards',
        });
        running.current = animation;
        animation.onfinish = () => {
            if (running.current !== animation) return;
            running.current = null;
            body.style.overflow = '';
            if (!open) setKept(false);
        };
    }, [open]);

    if (!kept) return null;
    return (
        <div ref={bodyRef} aria-hidden={open ? undefined : true}>
            {children}
        </div>
    );
}
