import React, { useCallback, useEffect, useRef, useState } from 'react';
import { TAB_BAR_SIDE, clampBellPosition } from '../../utils/mobileShell';
import { movedEnough } from '../../utils/dragGesture';

/* Колокол в углу телефона, который можно перетащить.
 *
 * ЗАЧЕМ. Колокол висит поверх содержимого (z-index 71) и в правом верхнем углу
 * закрывает собой то, что там нарисовал раздел. Живой случай: инструкция
 * аукциона смен — её крестик стоит ровно под колоколом, и закрыть инструкцию на
 * телефоне было нельзя вовсе, тап приходил колоколу. Место под кнопки в правом
 * верхнем углу занимает не один раздел, поэтому чинится это не перестановкой
 * крестика, а тем, что колокол можно отодвинуть (решение владельца 10.09.2026).
 *
 * ПОЧЕМУ top/left, А НЕ transform. Панель уведомлений лежит ВНУТРИ слота и
 * позиционируется как лист у нижней грани ЭКРАНА (position: fixed). Любой
 * transform на слоте сделал бы его точкой отсчёта для этого fixed, и лист
 * поехал бы за колоколом в угол — ровно та же ловушка, что была со шторкой
 * разделов. Поэтому колокол двигается координатами, а не сдвигом.
 *
 * ПОЧЕМУ ПОЗИЦИЯ НАЧИНАЕТСЯ С null. Пока человек колокол не трогал, место ему
 * задаёт CSS — верхний правый угол с учётом env(safe-area-inset-*). Проставь мы
 * координаты сразу, пришлось бы повторять эту арифметику в JS и разойтись с
 * ней на первом же устройстве с вырезом.
 */

const STORAGE_PREFIX = 'otp_mobile_bell:';

const storageKey = (userId) => `${STORAGE_PREFIX}${userId}`;

/* Приватный режим и отключённое хранилище: исключение прилетает уже на
   ОБРАЩЕНИИ к localStorage, поэтому в try завёрнуто всё. */
const readStored = (userId) => {
    try {
        const raw = window.localStorage.getItem(storageKey(userId));
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return null;
        if (!Number.isFinite(Number(parsed.x)) || !Number.isFinite(Number(parsed.y))) return null;
        return { x: Number(parsed.x), y: Number(parsed.y) };
    } catch (error) {
        return null;
    }
};

const writeStored = (userId, value) => {
    try {
        window.localStorage.setItem(storageKey(userId), JSON.stringify(value));
    } catch (error) {
        /* Место кончилось или хранилище закрыто — колокол просто вернётся в
           угол после перезагрузки. Ронять из-за этого уведомления нельзя. */
    }
};

const viewportSize = () => ({
    width: window.innerWidth || 390,
    height: window.innerHeight || 844,
});

/* Вырезы устройства. В CSS они есть как env(safe-area-inset-*), а в JS их не
   отдаёт ни один API: getPropertyValue у пользовательского свойства возвращает
   текст «max(10px, env(...))», а не пиксели. Поэтому замеряем пробником —
   невидимой коробкой, у которой эти же env стоят отступами. */
const readSafeArea = () => {
    if (typeof document === 'undefined') return { top: 0, right: 0, bottom: 0, left: 0 };
    const probe = document.createElement('div');
    probe.style.cssText = [
        'position:fixed', 'top:0', 'left:0', 'width:0', 'height:0',
        'visibility:hidden', 'pointer-events:none',
        'padding-top:env(safe-area-inset-top)',
        'padding-right:env(safe-area-inset-right)',
        'padding-bottom:env(safe-area-inset-bottom)',
        'padding-left:env(safe-area-inset-left)',
    ].join(';');
    document.body.appendChild(probe);
    const style = window.getComputedStyle(probe);
    const px = (value) => {
        const num = parseFloat(value);
        return Number.isFinite(num) ? num : 0;
    };
    const insets = {
        top: px(style.paddingTop),
        right: px(style.paddingRight),
        bottom: px(style.paddingBottom),
        left: px(style.paddingLeft),
    };
    probe.remove();
    return insets;
};

export default function MobileBellSlot({ userId, side = TAB_BAR_SIDE.BOTTOM, children }) {
    const slotRef = useRef(null);
    const dragRef = useRef(null);
    /* Клик после перетаскивания. Кнопку колокола рисует NotificationsBell, её
       onClick нам не принадлежит, — поэтому клик, доехавший до неё после
       жеста, гасим на перехвате. Иначе каждое перетаскивание заканчивалось бы
       раскрытым листом уведомлений. */
    const suppressClickRef = useRef(false);
    const safeAreaRef = useRef(null);
    const sideRef = useRef(side);
    const [position, setPosition] = useState(null);
    const [dragging, setDragging] = useState(false);

    useEffect(() => { sideRef.current = side; }, [side]);

    const safeArea = useCallback(() => {
        if (!safeAreaRef.current) safeAreaRef.current = readSafeArea();
        return safeAreaRef.current;
    }, []);

    /* Сохранённое место. Загоняем в экран сразу: координаты пережили поворот
       телефона, а то и переезд на другой аппарат. */
    useEffect(() => {
        if (!userId) return;
        const stored = readStored(userId);
        if (!stored) {
            setPosition(null);
            return;
        }
        setPosition(clampBellPosition(stored, viewportSize(), sideRef.current, safeArea()));
    }, [userId, safeArea]);

    useEffect(() => {
        if (!userId || !position || dragging) return;
        writeStored(userId, position);
    }, [userId, position, dragging]);

    /* Поворот и смена размеров окна. Место под баром переезжает к другой грани,
       и колокол, стоявший у неё, оказался бы под кнопками разделов. */
    useEffect(() => {
        const onResize = () => {
            safeAreaRef.current = null;
            setPosition((prev) => (
                prev ? clampBellPosition(prev, viewportSize(), sideRef.current, safeArea()) : prev
            ));
        };
        window.addEventListener('resize', onResize);
        window.addEventListener('orientationchange', onResize);
        return () => {
            window.removeEventListener('resize', onResize);
            window.removeEventListener('orientationchange', onResize);
        };
    }, [safeArea]);

    useEffect(() => {
        setPosition((prev) => (
            prev ? clampBellPosition(prev, viewportSize(), side, safeArea()) : prev
        ));
    }, [side, safeArea]);

    /* Жест ведут слушатели ОКНА, а не сам слот, и это не вкусовщина.
       Пробовали иначе — оба очевидных способа ломаются:
         * setPointerCapture на нажатии перенаправляет на слот и мышиные
           события, из которых браузер собирает click; тап по колоколу
           переставал открывать уведомления вовсе;
         * без захвата и без окна pointermove уходит тому, что под курсором:
           палец обгоняет колокол на первом же рывке, и жест срывается, не
           начавшись (на мыши — сразу, на пальце — как только он сойдёт с
           кнопки).
       Окно получает событие в любом случае — оно всплывает туда откуда угодно,
       — а click при этом остаётся кнопкиным. */
    const detachRef = useRef(null);

    const onPointerDown = useCallback((event) => {
        if (event.button != null && event.button !== 0) return;
        if (dragRef.current) return;
        const rect = slotRef.current?.getBoundingClientRect();
        const drag = {
            pointerId: event.pointerId,
            start: { x: event.clientX, y: event.clientY },
            /* Точка отсчёта — то, где колокол СЕЙЧАС на экране, а не сохранённая
               позиция: до первого перетаскивания её нет вовсе, место задаёт CSS. */
            origin: position || (rect ? { x: rect.left, y: rect.top } : { x: 0, y: 0 }),
            moved: false,
        };
        dragRef.current = drag;

        const detach = () => {
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
            window.removeEventListener('pointercancel', onUp);
            detachRef.current = null;
        };

        function onMove(moveEvent) {
            if (moveEvent.pointerId !== drag.pointerId) return;
            const current = { x: moveEvent.clientX, y: moveEvent.clientY };
            if (!drag.moved && !movedEnough(drag.start, current)) return;
            if (!drag.moved) {
                drag.moved = true;
                setDragging(true);
            }
            setPosition(clampBellPosition({
                x: drag.origin.x + (current.x - drag.start.x),
                y: drag.origin.y + (current.y - drag.start.y),
            }, viewportSize(), sideRef.current, safeArea()));
        }

        function onUp(upEvent) {
            if (upEvent.pointerId !== drag.pointerId) return;
            dragRef.current = null;
            detach();
            if (!drag.moved) return;   // нажатие без движения — обычный тап по колоколу
            setDragging(false);
            suppressClickRef.current = true;
            /* Место берём из САМОГО отпускания, а не из последнего pointermove:
               на быстром броске браузер имеет право схлопнуть события и не
               прислать move, совпадающий с отпусканием, — колокол остался бы в
               стороне от пальца. */
            setPosition(clampBellPosition({
                x: drag.origin.x + (upEvent.clientX - drag.start.x),
                y: drag.origin.y + (upEvent.clientY - drag.start.y),
            }, viewportSize(), sideRef.current, safeArea()));
            /* Жест без клика (браузер его не синтезирует, если палец ушёл
               далеко) не должен оставить заслонку взведённой — иначе она съест
               СЛЕДУЮЩИЙ тап по колоколу, уже настоящий. */
            window.setTimeout(() => { suppressClickRef.current = false; }, 400);
        }

        detachRef.current = detach;
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
        window.addEventListener('pointercancel', onUp);
    }, [position, safeArea]);

    /* Уход со страницы посреди жеста: слушатели окна переживут размонтирование
       и будут двигать колокола, которого уже нет. */
    useEffect(() => () => { detachRef.current?.(); }, []);

    const onClickCapture = useCallback((event) => {
        if (!suppressClickRef.current) return;
        suppressClickRef.current = false;
        event.stopPropagation();
        event.preventDefault();
    }, []);

    return (
        <div
            ref={slotRef}
            className="mobile-bell-slot"
            data-dragging={dragging ? '' : undefined}
            style={position ? { top: position.y, left: position.x, right: 'auto' } : undefined}
            onPointerDown={onPointerDown}
            onClickCapture={onClickCapture}
        >
            {children}
        </div>
    );
}
