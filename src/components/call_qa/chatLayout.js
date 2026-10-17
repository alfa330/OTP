import { useEffect, useRef, useState } from 'react';

/* Раскладка «текст слева, чат справа» экранов «Сводки» (день и период).
 *
 * useStickyHeight — высота чата на широком экране: от текущего верха колонки
 * до низа видимой области прокрутчика портала. Поле ввода видно сразу, пока
 * колонка ещё под шапкой раздела, а когда она прилипла к верху, чат вырастает
 * на всю высоту и прокручивает только свою ленту.
 *
 * useWide — колонка чата видна: на широком экране всегда, на узком — на
 * вкладке «Спросить ИИ». Нужна ленте: у скрытой (display:none) прокрутка не
 * действует.
 */

const WIDE = '(min-width: 1280px)';

export function useStickyHeight(enabled) {
    const ref = useRef(null);
    const [height, setHeight] = useState(null);
    useEffect(() => {
        if (!enabled || typeof window === 'undefined') { setHeight(null); return undefined; }
        const wide = window.matchMedia(WIDE);
        const node = ref.current;
        const scroller = node?.closest('.main-content') || null;
        let frame = 0;
        const recompute = () => {
            frame = 0;
            const column = ref.current;
            if (!wide.matches || !column) { setHeight(null); return; }
            const bounds = scroller ? scroller.getBoundingClientRect() : { top: 0, bottom: window.innerHeight };
            const top = Math.max(column.getBoundingClientRect().top, bounds.top + 16);
            const next = Math.max(420, Math.round(bounds.bottom - top - 16));
            setHeight((prev) => (prev === next ? prev : next));
        };
        const schedule = () => { if (!frame) frame = window.requestAnimationFrame(recompute); };
        recompute();
        window.addEventListener('resize', schedule);
        (scroller || window).addEventListener('scroll', schedule, { passive: true });
        wide.addEventListener?.('change', schedule);
        return () => {
            if (frame) window.cancelAnimationFrame(frame);
            window.removeEventListener('resize', schedule);
            (scroller || window).removeEventListener('scroll', schedule);
            wide.removeEventListener?.('change', schedule);
        };
    }, [enabled]);
    return [ref, height];
}

export function useWide() {
    const [wide, setWide] = useState(() => typeof window !== 'undefined' && window.matchMedia?.(WIDE).matches);
    useEffect(() => {
        if (typeof window === 'undefined' || !window.matchMedia) return undefined;
        const media = window.matchMedia(WIDE);
        const update = () => setWide(media.matches);
        update();
        media.addEventListener?.('change', update);
        return () => media.removeEventListener?.('change', update);
    }, []);
    return wide;
}
