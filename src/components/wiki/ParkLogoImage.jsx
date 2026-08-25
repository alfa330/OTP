import React from 'react';
import { frameStyle } from './parkLogo';

/* Логотип парка внутри квадратной плитки, с учётом выбранного ракурса.
 *
 * Один компонент на все четыре места показа — рельс витрины, карточка
 * справочника, шапка страницы парка и сама форма: до ракурса это была
 * одинаковая строчка `object-cover` в каждом из них, а с ракурсом стала бы
 * одинаковая арифметика. Четыре её копии разошлись бы на первой же правке, и
 * логотип показывался бы в форме иначе, чем на главной, — то есть ракурс
 * нельзя было бы выбрать, глядя на результат.
 *
 * Плитка-родитель обязана быть relative и overflow-hidden: картинка с ракурсом
 * позиционируется внутри неё абсолютно и по краям выходит за неё.
 */
export default function ParkLogoImage({ url, frame, alt = '', className = '' }) {
    if (!url) return null;
    const style = frameStyle(frame);
    return style
        ? <img src={url} alt={alt} style={style} draggable={false} className={className} />
        : (
            /* Ракурса нет — прежний показ: середина по короткой стороне. Так
               выглядят логотипы, загруженные до появления ракурса. */
            <img src={url} alt={alt} draggable={false}
                 className={`h-full w-full object-cover ${className}`} />
        );
}
