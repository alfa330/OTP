import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { hoverOpensHint } from './infoHintPress';

/**
 * Подсказка «что делает эта кнопка» — по наведению мыши.
 *
 * Пишется атрибутом, а не обёрткой: `<button data-tip="Убрать в архив…">`. Один
 * хозяин на экран (<HoverTips /> рядом с разделом) слушает документ и рисует
 * пузырь порталом в body. Обёртка-компонент вокруг каждой кнопки ломала бы
 * вёрстку там, где кнопка — прямой ребёнок flex-ряда или сетки (раскладки
 * телефона в wiki-mobile.css выбирают кнопки именно так), а порталу не страшен
 * ни overflow-hidden карточки, ни прокручиваемое тело модалки.
 *
 * Вид — тот же тёмный пузырь со стрелкой, что у подсказок графика смен
 * (ScheduleTimelineTooltip): одна подсказка портала, а не вторая со своим видом.
 * Поведение — как у подсказок к кнопкам в macOS, и в этом разница с графиком:
 *
 *  - только мышь. Касание браузер тоже превращает в «наведение», и пузырь
 *    вставал бы на телефоне поверх того, что человек только что нажал;
 *  - с задержкой: пузырь, вспыхивающий от мыши, проезжающей мимо ряда кнопок,
 *    и есть визуальный шум;
 *  - нажатие гасит пузырь, и до ухода мыши с кнопки он не возвращается: что
 *    кнопка делает, человек уже решил, а пузырь поверх открывшегося окна — мусор;
 *  - переход по Tab показывает сразу; фокус от щелчка или возвращённый скриптом
 *    (Escape в панели «Фильтр») — нет: иначе подсказка висела бы над кнопкой,
 *    которую только что нажали или закрыли;
 *  - любая клавиша гасит; прокрутка гасит пузырь от мыши (от уехавшей кнопки он
 *    висел бы над чужой строкой), а пузырь от Tab переставляет;
 *  - кнопка под открытым окном пузыря не даёт (anchorOnTop).
 *
 * Отключённая кнопка подсказки не даёт: «Следующий день» на сегодняшней дате
 * ничего не делает, и обещать действие ей незачем.
 */

const SELECTOR = '[data-tip]';
export const TIP_SHOW_DELAY_MS = 400;
const VIEWPORT_GAP = 8;
const ANCHOR_GAP = 8;
const ARROW_EDGE_GAP = 12;

const clamp = (value, min, max) => Math.min(Math.max(value, min), Math.max(min, max));

/** Текст подсказки элемента — обрезанный; пустая строка — подсказки нет. */
export const tipTextOf = (anchor) => String(anchor?.getAttribute?.('data-tip') || '').trim();

/** Элемент с подсказкой под указателем — или null. Отключённый не считается, и
 *  раскрытый тоже: у «Фильтра» с открытой панелью или у даты с открытым
 *  календарём пузырь «что делает кнопка» лёг бы поверх того, что она уже сделала. */
export const tipAnchorOf = (target) => {
    if (typeof Element === 'undefined' || !(target instanceof Element)) return null;
    const anchor = target.closest(SELECTOR);
    if (!anchor || !tipTextOf(anchor)) return null;
    if (anchor.matches(':disabled, [aria-disabled="true"], [aria-expanded="true"]')) return null;
    return anchor;
};

/**
 * Где встать пузырю: { left, top, placement, arrowLeft } в координатах окна.
 * Чистый расчёт без DOM — его читают тесты.
 *
 *   anchor — прямоугольник кнопки { left, top, width, bottom };
 *   tip    — размер пузыря { width, height };
 *   view   — окно { width, height }.
 *
 * Над кнопкой, если сверху хватает места (или сверху его больше, чем снизу),
 * иначе под ней; по горизонтали — по центру кнопки, но не за краем окна, а
 * стрелка остаётся над кнопкой, даже когда сам пузырь прижат к краю.
 */
export const tipPlacement = ({ anchor, tip, view }) => {
    const spaceAbove = anchor.top - VIEWPORT_GAP;
    const spaceBelow = view.height - anchor.bottom - VIEWPORT_GAP;
    const placement = spaceAbove >= tip.height + ANCHOR_GAP || spaceAbove >= spaceBelow
        ? 'top' : 'bottom';
    const center = anchor.left + anchor.width / 2;
    const left = clamp(center - tip.width / 2, VIEWPORT_GAP, view.width - VIEWPORT_GAP - tip.width);
    const top = clamp(
        placement === 'top' ? anchor.top - ANCHOR_GAP - tip.height : anchor.bottom + ANCHOR_GAP,
        VIEWPORT_GAP,
        view.height - VIEWPORT_GAP - tip.height,
    );
    const arrowLeft = clamp(center - left, ARROW_EDGE_GAP, tip.width - ARROW_EDGE_GAP);
    return { left: Math.round(left), top: Math.round(top), placement, arrowLeft: Math.round(arrowLeft) };
};

/* Фокус показывает подсказку, только если его перевёл Tab. Проверки
   :focus-visible для этого мало: панель «Фильтр» и календарь по Escape сами
   возвращают фокус на свою кнопку, последнее действие было клавишей — и пузырь
   вставал над кнопкой, которую только что закрыли, хотя никто к ней не
   переходил. А фокус от Tab и так всегда :focus-visible — отдельно его не
   спрашиваем. Щелчок мышью отметку Tab стирает (onDown). */
export const TAB_FOCUS_WINDOW_MS = 500;
export const focusCameFromTab = (tabAt, now) => (
    tabAt != null && now >= tabAt && now - tabAt <= TAB_FOCUS_WINDOW_MS
);

/* Видна ли кнопка сверху — или её закрывает открытое окно. IosModal фокус в
   себе не держит: Tab при открытой карточке офиса ходит по кнопкам таблицы ПОД
   затемнением, и без этой проверки пузырь «Убрать в архив…» вставал поверх окна
   со стрелкой на кнопку, которой не видно. Мышью так не бывает — затемнение
   само ловит наведение. Нечем проверить (нет elementFromPoint, кнопка за краем
   окна) — считаем видимой: лучше лишний пузырь, чем пропавшая подсказка. */
export const anchorOnTop = (anchor, doc = typeof document === 'undefined' ? null : document) => {
    if (!anchor?.getBoundingClientRect || typeof doc?.elementFromPoint !== 'function') return true;
    const rect = anchor.getBoundingClientRect();
    const hit = doc.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return !hit || hit === anchor || anchor.contains(hit);
};

export default function HoverTips() {
    /* { anchor, text, source, pos } — одним объектом: место пузыря посчитано под
       этот текст у этой кнопки, и разойтись с ними оно не должно. source —
       'hover' или 'focus' (Tab). pos: null — пузырь ещё не измерен и стоит
       невидимым. */
    const [tip, setTip] = useState(null);
    const bubbleRef = useRef(null);
    const timer = useRef(null);
    // Кнопка под мышью — и кнопка, на которой нажали: до ухода мыши с неё молчим.
    const hovered = useRef(null);
    const pressed = useRef(null);
    // Когда последний раз нажимали Tab (performance.now()), см. focusCameFromTab.
    const tabbedAt = useRef(null);

    const cancel = useCallback(() => {
        if (timer.current) {
            clearTimeout(timer.current);
            timer.current = null;
        }
    }, []);

    const hide = useCallback(() => {
        cancel();
        setTip(null);
    }, [cancel]);

    const show = useCallback((anchor, source = 'hover') => {
        cancel();
        const text = tipTextOf(anchor);
        if (!anchor?.isConnected || !text) {
            setTip(null);
            return;
        }
        setTip((current) => (current?.anchor === anchor && current.text === text
            ? current : { anchor, text, source, pos: null }));
    }, [cancel]);

    useEffect(() => {
        if (typeof document === 'undefined') return undefined;

        const onOver = (event) => {
            if (!hoverOpensHint(event.pointerType)) return;
            // Человек взялся за мышь — пузырь, показанный по Tab, своё отжил: он
            // висел бы над кнопкой с фокусом, пока мышь водят по соседним.
            setTip((current) => (current?.source === 'focus' ? null : current));
            const anchor = tipAnchorOf(event.target);
            if (anchor === hovered.current) return;
            hovered.current = anchor;
            if (anchor !== pressed.current) pressed.current = null;
            hide();
            if (!anchor || anchor === pressed.current) return;
            timer.current = setTimeout(() => {
                timer.current = null;
                if (hovered.current === anchor) show(anchor);
            }, TIP_SHOW_DELAY_MS);
        };
        // Мышь ушла за пределы страницы: pointerover больше не придёт.
        const onOut = (event) => {
            if (!hoverOpensHint(event.pointerType) || event.relatedTarget) return;
            hovered.current = null;
            pressed.current = null;
            hide();
        };
        const onDown = (event) => {
            pressed.current = tipAnchorOf(event.target) || hovered.current;
            tabbedAt.current = null;
            hide();
        };
        const onFocusIn = (event) => {
            const anchor = tipAnchorOf(event.target);
            if (anchor && focusCameFromTab(tabbedAt.current, performance.now()) && anchorOnTop(anchor)) {
                show(anchor, 'focus');
            }
        };
        const onFocusOut = () => {
            setTip((current) => (current && current.anchor !== hovered.current ? null : current));
        };
        // Клавиша гасит; Tab переведёт фокус дальше, и focusin покажет подсказку
        // уже новой кнопки — keydown приходит раньше, чем уходит фокус.
        const onKey = (event) => {
            tabbedAt.current = event.key === 'Tab' ? performance.now() : null;
            hide();
        };
        /* Прокрутка гасит пузырь от мыши: кнопка уехала из-под курсора. Пузырь
           от Tab — переставляем: страницу докрутил сам браузер, чтобы показать
           кнопку, получившую фокус, и гасить подсказку ровно на ней незачем. */
        const onScroll = () => {
            cancel();
            setTip((current) => {
                if (!current) return current;
                const focused = current.source === 'focus' && current.anchor.isConnected
                    && document.activeElement === current.anchor;
                return focused ? { ...current, pos: null } : null;
            });
        };

        document.addEventListener('pointerover', onOver);
        document.addEventListener('pointerout', onOut);
        document.addEventListener('pointerdown', onDown, true);
        document.addEventListener('focusin', onFocusIn);
        document.addEventListener('focusout', onFocusOut);
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', onScroll);
        return () => {
            document.removeEventListener('pointerover', onOver);
            document.removeEventListener('pointerout', onOut);
            document.removeEventListener('pointerdown', onDown, true);
            document.removeEventListener('focusin', onFocusIn);
            document.removeEventListener('focusout', onFocusOut);
            document.removeEventListener('keydown', onKey, true);
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', onScroll);
            cancel();
        };
    }, [cancel, hide, show]);

    /* Размер пузыря известен только после отрисовки, поэтому первый кадр он
       стоит невидимым, а место ему считается здесь — до показа кадра. */
    useLayoutEffect(() => {
        if (!tip || tip.pos) return;
        const bubble = bubbleRef.current;
        // Кнопку убрали или закрыли окном, пока пузырь ждал места, — не рисуем.
        if (!bubble || !tip.anchor.isConnected || !anchorOnTop(tip.anchor)) {
            setTip(null);
            return;
        }
        const rect = tip.anchor.getBoundingClientRect();
        const box = bubble.getBoundingClientRect();
        const pos = tipPlacement({
            anchor: { left: rect.left, top: rect.top, width: rect.width, bottom: rect.bottom },
            tip: { width: box.width, height: box.height },
            view: {
                width: document.documentElement?.clientWidth || window.innerWidth,
                height: document.documentElement?.clientHeight || window.innerHeight,
            },
        });
        setTip((current) => (current === tip ? { ...current, pos } : current));
    }, [tip]);

    if (!tip || typeof document === 'undefined' || !document.body) return null;

    const { pos } = tip;
    const placement = pos?.placement || 'top';
    /* z-index выше любого слоя портала: подсказка нужна и кнопкам в окне
       (IosModal, z-90), и в панели фильтра (z 99999). Пузырь сам не ловит мышь
       (pointer-events: none), так что ничего под собой не перекрывает. */
    return createPortal(
        <div
            ref={bubbleRef}
            role="tooltip"
            className="pointer-events-none fixed z-[100000] w-max max-w-[min(18rem,calc(100vw-1rem))] whitespace-pre-line break-words rounded-xl border border-white/10 bg-slate-950/95 px-3 py-2 text-left text-[12px] font-medium leading-4 text-white shadow-[0_12px_32px_rgba(15,23,42,0.3)] backdrop-blur-md motion-safe:animate-[fadeIn_.12s_ease-out]"
            style={{
                left: pos?.left ?? 0,
                top: pos?.top ?? 0,
                visibility: pos ? 'visible' : 'hidden',
            }}
        >
            {tip.text}
            <span
                aria-hidden="true"
                className={`absolute h-2.5 w-2.5 -translate-x-1/2 rotate-45 bg-slate-950/95 ${
                    placement === 'top'
                        ? '-bottom-[5px] border-b border-r border-white/10'
                        : '-top-[5px] border-l border-t border-white/10'
                }`}
                style={{ left: pos?.arrowLeft ?? '50%' }}
            />
        </div>,
        document.body,
    );
}
