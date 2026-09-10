import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';

/* Верхняя полоса телефона — матовое стекло вместо мёртвого поля под вырезом.
 *
 * ЧТО БЫЛО НЕ ТАК. Место под угловой колокол оболочка отбивала верхним
 * отступом у .main-content, и полоса красилась ЕГО фоном — серым. Раздел под
 * ней почти всегда своего цвета (у «Профиля» белый), поэтому сверху шла
 * чужеродная полоса поперёк экрана, а в установленном приложении в неё
 * попадал ещё и вырез. Владелец назвал это «чёлка другого цвета» и попросил
 * «как ликвид гласс» — чтобы верх подстраивался под страницу.
 *
 * КАК СДЕЛАНО. Два независимых слоя:
 *
 * 1. ЦВЕТ СТРАНИЦЫ. Раз в смену раздела читаем фон его корневого блока и
 *    кладём в переменную --mobile-page-bg; ею красится .main-content. Если у
 *    корня раздела фона нет (прозрачный) — оставляем свой, и шва всё равно не
 *    будет: под полосой и под ней самой окажется один и тот же цвет. Читаем
 *    ВЫЧИСЛЕННЫЙ стиль, а не гадаем по классам: у разделов они разные
 *    (bg-white, bg-slate-50, градиент), и списка «какой раздел какого цвета»
 *    быть не должно — он разъедется с разметкой на первой же правке.
 *
 * 2. СТЕКЛО. Поверх полосы лежит слой с backdrop-filter. Пока страница не
 *    прокручена, за ним ровный цвет страницы и полосы не видно вовсе; стоит
 *    прокрутить — под него уезжает содержимое раздела, стекло набирает его
 *    цвета, и снизу проявляется волосяная линия. Это и есть «подстраивается
 *    под страницу»: полоса не крашена заранее, а показывает то, что под ней.
 *
 * ОДИН СЛУШАТЕЛЬ ПРОКРУТКИ НА ВСЮ ОБОЛОЧКУ. Он же поднимает флаг для имени в
 * шапке «Профиля» (MobileScrollTitle читает тот же признак стилями). Слушатель
 * пассивный и трогает состояние только на ПЕРЕХОДЕ через порог — иначе на
 * каждый кадр движения пальца шла бы перерисовка всего раздела.
 */

/* Порог, после которого страница считается прокрученной. Пара десятков
   пикселей — это дрожь пальца и «резинка» прокрутки, на них полоса моргала бы.
   Выше поднимать нельзя: у «Профиля» вся страница на телефоне прокручивается
   на 167 px, и порог в полэкрана просто не был бы достижим. */
const SCROLLED_AT = 24;

const isOpaque = (color) => {
    if (!color) return false;
    const value = String(color).trim();
    if (!value || value === 'transparent') return false;
    /* rgba(...,0) и rgba(...,0.04) — это «фона нет»: сквозь такой всё равно
       виден фон родителя, и красить им полосу значит красить её мимо. */
    const match = value.match(/^rgba?\(([^)]+)\)$/i);
    if (!match) return true;
    const parts = match[1].split(',').map((part) => parseFloat(part.trim()));
    if (parts.length < 4) return true;
    return Number.isFinite(parts[3]) && parts[3] > 0.5;
};

export default function MobileTopBar({ active, view }) {
    useEffect(() => {
        if (typeof document === 'undefined') return undefined;
        const { body } = document;
        if (!active) {
            delete body.dataset.mobileScrolled;
            body.style.removeProperty('--mobile-page-bg');
            return undefined;
        }

        let scrolled = null;
        const sync = () => {
            const next = window.scrollY > SCROLLED_AT;
            if (next === scrolled) return;
            scrolled = next;
            if (next) body.dataset.mobileScrolled = '1';
            else delete body.dataset.mobileScrolled;
        };
        sync();
        window.addEventListener('scroll', sync, { passive: true });

        /* Цвет читаем СЛЕДУЮЩИМ кадром: на кадре смены раздела старый блок уже
           снят, а новый ещё не отрисован, и getComputedStyle вернул бы цвет
           предыдущего экрана. */
        const frame = requestAnimationFrame(() => {
            const root = document.querySelector('.main-content')?.firstElementChild;
            const color = root ? getComputedStyle(root).backgroundColor : '';
            if (isOpaque(color)) body.style.setProperty('--mobile-page-bg', color);
            else body.style.removeProperty('--mobile-page-bg');
        });

        return () => {
            cancelAnimationFrame(frame);
            window.removeEventListener('scroll', sync, { passive: true });
            delete body.dataset.mobileScrolled;
            body.style.removeProperty('--mobile-page-bg');
        };
    }, [active, view]);

    if (!active || typeof document === 'undefined') return null;
    return createPortal(<div className="mobile-top-bar" aria-hidden="true" />, document.body);
}
