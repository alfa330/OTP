import React, { useEffect, useRef, useState } from 'react';

/* Имя в шапке профиля при прокрутке — как в списке настроек телефона.
 *
 * ЗАЧЕМ. Профиль на телефоне начинается крупным портретом и именем, а дальше
 * идёт длинный список разделов. Прокрутив его, человек терял всякий признак
 * того, чей это профиль. Владелец 11.09.2026 прислал видео (настройки
 * Telegram) и попросил так же: «чтобы имя пользователя сохранялось при
 * прокрутке в нижнюю часть».
 *
 * КАК. Полоса ЛИПКАЯ и лежит прямо в прокручиваемом листе — держится сама, без
 * единого обработчика прокрутки. Показать её надо ровно тогда, когда крупное
 * имя ушло под неё, и это решает наблюдатель за пересечением: он срабатывает
 * только на самом событии, а не на каждом кадре движения пальца.
 *
 * ПОЧЕМУ НАБЛЮДАТЕЛЬ ЗДЕСЬ ГОДИТСЯ, А НА СТРАНИЦЕ «ПРОФИЛЯ» НЕ ГОДИЛСЯ. Там
 * признак был недостижим: вся страница была короче экрана, и уехать под шапку
 * имени было некуда. Здесь под именем лежит полный список разделов — лист
 * длиннее экрана втрое; а если разделов у человека совсем мало и лист не
 * прокручивается, то и показывать полосу незачем.
 */

/* Ближайший предок, который на самом деле прокручивается. Искать надо именно
   так: лист рисуется внутри сайдбара, и высота прокрутки принадлежит не ему. */
const findScrollParent = (node) => {
    let current = node?.parentElement || null;
    while (current) {
        const overflowY = getComputedStyle(current).overflowY;
        if (/(auto|scroll)/.test(overflowY) && current.scrollHeight > current.clientHeight) {
            return current;
        }
        current = current.parentElement;
    }
    return null;
};

export default function MobileSheetTitle({ name = '', open = false }) {
    const barRef = useRef(null);
    const [shown, setShown] = useState(false);

    useEffect(() => {
        /* Лист закрыли — имя в шапке гасим сразу: открыв профиль снова, человек
           видит его сверху, а не в полосе от прошлого раза. */
        if (!open) {
            setShown(false);
            return undefined;
        }
        const bar = barRef.current;
        if (!bar || typeof IntersectionObserver !== 'function') return undefined;
        const scroller = findScrollParent(bar);
        const target = document.querySelector('.mobile-sheet-hero__name');
        if (!scroller || !target) return undefined;

        const observer = new IntersectionObserver(
            ([entry]) => setShown(!entry.isIntersecting),
            {
                root: scroller,
                /* Верхнюю кромку поднимаем на высоту самой полосы: имя считается
                   ушедшим, когда оно скрылось ПОД ней, а не когда коснулось
                   верхнего края листа. */
                rootMargin: `-${Math.round(bar.offsetHeight)}px 0px 0px 0px`,
                threshold: 0,
            },
        );
        observer.observe(target);
        return () => observer.disconnect();
    }, [open, name]);

    return (
        <div ref={barRef} className="mobile-sheet-topbar" data-shown={shown ? '' : undefined}>
            <span className="mobile-sheet-topbar__name">{name}</span>
        </div>
    );
}
