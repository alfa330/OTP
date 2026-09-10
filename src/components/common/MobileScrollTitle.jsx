import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

/* Шапка, которая проявляется при прокрутке — приём из Telegram и системных
 * «Настроек»: пока крупное имя с портретом видно, верх экрана пуст, а как
 * только они уезжают вверх, имя появляется строкой в шапке. Так человек всегда
 * знает, чей это экран, не листая обратно.
 *
 * ПОЧЕМУ НАБЛЮДАТЕЛЬ, А НЕ ОБРАБОТЧИК ПРОКРУТКИ. onscroll на телефоне зовётся
 * на каждый кадр движения пальца, и любой setState в нём — это перерисовка
 * раздела шестьдесят раз в секунду. IntersectionObserver будит нас ровно
 * дважды: когда крупное имя скрылось и когда вернулось.
 *
 * ПОЧЕМУ ПОРТАЛ В <body>. Шапка обязана лежать поверх раздела и считаться от
 * ЭКРАНА. Внутри .main-content ей мешают сразу двое: у контейнера раздела свой
 * overflow-x: hidden (он обрезал бы размытие по краям) и верхний отступ под
 * колокол — шапка встала бы на 56 пикселей ниже, чем нужно.
 *
 * Правое поле оставлено под угловой колокол: он висит в том же углу, и без
 * запаса длинное имя заезжало бы прямо под него.
 */
export default function MobileScrollTitle({ active, watch, title, avatarUrl, initial }) {
    const [shown, setShown] = useState(false);
    /* Портрет может не загрузиться — тогда, как и в баре разделов, показываем
       первую букву имени. Помним неудачу по адресу: иначе смена портрета в
       профиле не показала бы новую картинку. */
    const [brokenAvatar, setBrokenAvatar] = useState(null);
    const watchRef = watch;
    const shownRef = useRef(shown);
    shownRef.current = shown;

    useEffect(() => {
        if (!active) {
            setShown(false);
            return undefined;
        }
        const target = watchRef?.current;
        if (!target || typeof IntersectionObserver === 'undefined') return undefined;
        /* Порог по верхней грани: шапка появляется ровно тогда, когда крупное
           имя уходит ПОД неё, а не когда исчезает совсем с экрана. */
        const observer = new IntersectionObserver(
            (entries) => {
                const entry = entries[entries.length - 1];
                if (!entry) return;
                const next = !entry.isIntersecting && entry.boundingClientRect.top < 0;
                if (shownRef.current !== next) setShown(next);
            },
            { rootMargin: '-46px 0px 0px 0px', threshold: 0 },
        );
        observer.observe(target);
        return () => observer.disconnect();
    }, [active, watchRef]);

    if (!active || typeof document === 'undefined') return null;

    const avatarOk = Boolean(avatarUrl) && brokenAvatar !== avatarUrl;
    return createPortal(
        <div className={`mobile-scroll-title${shown ? ' is-shown' : ''}`} aria-hidden={!shown}>
            <span className="mobile-scroll-title__avatar">
                {avatarOk ? (
                    <img src={avatarUrl} alt="" onError={() => setBrokenAvatar(avatarUrl)} />
                ) : (
                    <span>{String(initial || title || 'U').charAt(0).toUpperCase()}</span>
                )}
            </span>
            <span className="mobile-scroll-title__text">{title}</span>
        </div>,
        document.body,
    );
}
