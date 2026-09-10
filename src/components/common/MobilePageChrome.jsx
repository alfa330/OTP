import { useEffect } from 'react';

/* Обвес страницы на телефоне: цвет полосы состояния и признак «прокручено».
 *
 * Разметки у этого компонента нет вовсе — он ничего не рисует. Своей полосы
 * поверх раздела здесь была, и владелец 10.09.2026 попросил её убрать: «убрать
 * прозрачную полоску сверху, я имел в виду вот есть же чёлка телефона, где
 * отображается заряд и т.д., данную полоску сделать адаптивной». Речь про
 * СИСТЕМНУЮ полосу состояния установленного приложения, а не про свою.
 *
 * КАК КРАСИТСЯ СИСТЕМНАЯ ПОЛОСА. В установленном приложении её цвет берётся
 * из <meta name="theme-color">, а не из стилей: до неё CSS не достаёт вовсе.
 * Поэтому на каждую смену раздела читаем вычисленный фон его корневого блока и
 * кладём и в переменную --mobile-page-bg (ею красится .main-content, чтобы под
 * вырезом не шла чужеродная полоса), и в сам meta. Цвет букв и значков система
 * подбирает по нему сама — специально его считать не надо.
 *
 * ПОЧЕМУ ЧИТАЕМ СТИЛЬ, А НЕ ДЕРЖИМ СПИСОК РАЗДЕЛОВ. Классы у разделов разные
 * (bg-white, bg-slate-50, градиент), и список «какой раздел какого цвета»
 * разъедется с разметкой на первой же правке.
 *
 * ПРИЗНАК «ПРОКРУЧЕНО» нужен имени в шапке «Профиля». Слушатель один на всю
 * оболочку, пассивный, и трогает состояние только на ПЕРЕХОДЕ через порог:
 * onscroll на телефоне зовётся на каждый кадр движения пальца.
 */

/* Порог, после которого страница считается прокрученной. Пара десятков
   пикселей — это дрожь пальца и «резинка» прокрутки, на них имя моргало бы.
   Выше поднимать нельзя: у «Профиля» вся страница на телефоне прокручивается
   на 167 px, и порог в полэкрана просто не был бы достижим. */
const SCROLLED_AT = 24;

/* Цвет полосы состояния, когда раздел своего не дал. Совпадает с меткой в
   index.html: без раздела (экран входа) полоса должна остаться той же. */
const FALLBACK_BG = '#f9fafb';

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

export default function MobilePageChrome({ active, view }) {
    useEffect(() => {
        if (typeof document === 'undefined') return undefined;
        const { body } = document;
        const meta = document.querySelector('meta[name="theme-color"]');
        if (!active) {
            delete body.dataset.mobileScrolled;
            body.style.removeProperty('--mobile-page-bg');
            meta?.setAttribute('content', FALLBACK_BG);
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
            if (isOpaque(color)) {
                body.style.setProperty('--mobile-page-bg', color);
                meta?.setAttribute('content', color);
            } else {
                body.style.removeProperty('--mobile-page-bg');
                meta?.setAttribute('content', FALLBACK_BG);
            }
        });

        return () => {
            cancelAnimationFrame(frame);
            window.removeEventListener('scroll', sync, { passive: true });
            delete body.dataset.mobileScrolled;
            body.style.removeProperty('--mobile-page-bg');
            meta?.setAttribute('content', FALLBACK_BG);
        };
    }, [active, view]);

    return null;
}
