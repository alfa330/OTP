import React, { useState } from 'react';
import { createPortal } from 'react-dom';

/* Имя в шапке при прокрутке — приём из Telegram: пока экран не тронут, верх
 * пуст, а стоит прокрутить, как имя с портретом появляется строкой сверху.
 *
 * ПОЧЕМУ ПРОСТО «СТРАНИЦА ПРОКРУЧЕНА», А НЕ «КРУПНОЕ ИМЯ УЕХАЛО ПОД ШАПКУ».
 * Второе — правильнее по смыслу и было сделано первым (наблюдатель за крупным
 * портретом), но НЕ СРАБАТЫВАЛО НИ РАЗУ: у «Профиля» вся страница на телефоне
 * 1011 px при экране 844, то есть прокрутить её можно на 167 px, а крупное имя
 * стоит на 435-м — под шапку оно не доезжает в принципе. Владелец так и
 * сказал: «имя не прокручивается». Признак теперь один и достижимый —
 * страница сдвинулась больше чем на дрожь пальца; поднимает его MobileTopBar
 * одним пассивным слушателем на всю оболочку, а здесь он читается СТИЛЯМИ
 * (body[data-mobile-scrolled]), поэтому у самого имени состояния нет вовсе и
 * прокрутка не вызывает перерисовок.
 *
 * ПОЧЕМУ ПОРТАЛ В <body>. Шапка обязана лежать поверх раздела и считаться от
 * ЭКРАНА. Внутри .main-content ей мешают сразу двое: у контейнера раздела свой
 * overflow-x (он обрезал бы размытие по краям) и верхний отступ под колокол —
 * шапка встала бы ниже, чем нужно.
 *
 * Правое поле оставлено под угловой колокол: он висит в том же углу, и без
 * запаса длинное имя заезжало бы прямо под него.
 */
export default function MobileScrollTitle({ active, title, avatarUrl, initial }) {
    /* Портрет может не загрузиться — тогда, как и в баре разделов, показываем
       первую букву имени. Помним неудачу по адресу: иначе смена портрета в
       профиле не показала бы новую картинку. */
    const [brokenAvatar, setBrokenAvatar] = useState(null);

    if (!active || typeof document === 'undefined') return null;

    const avatarOk = Boolean(avatarUrl) && brokenAvatar !== avatarUrl;
    return createPortal(
        <div className="mobile-scroll-title">
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
