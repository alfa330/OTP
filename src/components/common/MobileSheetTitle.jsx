import React, { useEffect, useRef } from 'react';

/* Имя в шапке профиля при прокрутке — как в настройках Telegram.
 *
 * ЗАЧЕМ. Профиль на телефоне начинается крупным портретом и именем, а дальше
 * идёт длинный список разделов. Прокрутив его, человек терял всякий признак
 * того, чей это профиль. Владелец 11.09.2026 прислал видео (настройки
 * Telegram) и попросил так же: «чтобы имя пользователя сохранялось при
 * прокрутке в нижнюю часть».
 *
 * ПЕРЕХОД ПРИВЯЗАН К ПАЛЬЦУ (владелец 17.09.2026, снимки Telegram на iOS 26:
 * «сделай все как в телеграмме, чтобы было ликвид гласс, и имя переливалось в
 * бокс плавно»). Раньше полоса ПРОЯВЛЯЛАСЬ разом по наблюдателю за
 * пересечением: крупное имя уезжало, а маленькое возникало отдельным
 * движением. Теперь это одно движение: крупное имя, подъезжая к строке,
 * уменьшается до её размера и в ней остаётся, портрет сжимается к верху, а
 * стекло набирается, как только под шапку уходят строки. Для этого нужна ДОЛЯ
 * пути на каждом кадре, а не событие «ушло / не ушло» — поэтому здесь слушатель
 * прокрутки. Он пассивный, живёт только пока шторка открыта, пишет переменные
 * CSS не чаще кадра и мимо React: перерисовок нет вовсе. Положения крупного
 * имени и портрета меряются один раз при открытии (и при повороте) через
 * offsetTop — на смещения transform не влияет, поэтому собственное движение
 * имени не сбивает замер.
 *
 * ПОЧЕМУ ШАПКА ПРИБИТА К ЛИСТУ, А НЕ ЛИПКАЯ. Полоса с часами над шапкой — место
 * стекла iOS 26, и WebKit заменяет его сплошной заливкой, если у верхней грани
 * находит fixed или sticky элемент во всю ширину (см. «Верх экрана отдан стеклу
 * iOS 26» в mobile-shell.css). Шапка — position: absolute от самого листа: она
 * стоит на месте при прокрутке списка, закрывает стеклом и полосу с часами, и
 * строку имени, а правило WebKit её не видит — ищет только fixed и sticky.
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

/* Путь, за который крупное имя доходит до строки: на нём оно уменьшается до
   размера строки. Маленькое имя всё это время стоит РОВНО на месте крупного
   и того же размера, поэтому смена одного на другое глазу не видна: одно имя
   въезжает в строку. Первая версия меняла их с разных мест, и на середине пути
   «Корневой Директор» читался дважды (кадры 17.09.2026).

   Смена — ДО стекла: пока крупное имя ещё ниже шапки. Дальше в строку едет уже
   маленькое, поверх стекла и чёткое; иначе крупное успевало заехать под стекло
   и мелькало размытым. Числа — расстояние от центра имени до центра строки. */
const NAME_TRAVEL = 72;
const HANDOFF_FROM = 70;
const HANDOFF_SPAN = 22;
/* Кегль строки. Маленькое имя НАБРАНО тем же кеглем, что крупное, и доведено до
   строки масштабом: набранные разными кеглями, слова расходились по ширине на
   пару пикселей — браузер раскладывает буквы для каждого кегля по-своему. */
const ROW_FONT_SIZE = 17;
/* Стекло набирается за этот путь, как только под шапку зашла первая строка. */
const GLASS_RANGE = 24;

const clamp01 = (value) => (value < 0 ? 0 : value > 1 ? 1 : value);

/* Сдвиг элемента от верха прокручиваемого блока в координатах содержимого. */
const offsetWithin = (node, ancestor) => {
    let top = 0;
    let current = node;
    while (current && current !== ancestor && ancestor.contains(current)) {
        top += current.offsetTop;
        current = current.offsetParent;
    }
    if (current !== ancestor) {
        /* offsetParent вышел за блок (сам блок не позиционирован) — считаем от
           его собственного сдвига внутри того же предка. */
        top -= ancestor.offsetTop;
    }
    return top;
};

export default function MobileSheetTitle({ name = '', open = false }) {
    const barRef = useRef(null);

    useEffect(() => {
        const bar = barRef.current;
        const block = bar?.parentElement;
        const reset = () => {
            if (!block) return;
            block.style.setProperty('--sheet-title', '0');
            block.style.removeProperty('--sheet-title-y');
            block.style.removeProperty('--sheet-title-scale');
            block.style.removeProperty('--sheet-name-scale');
            block.style.setProperty('--sheet-glass', '0');
            block.style.setProperty('--sheet-avatar', '0');
        };
        /* Лист закрыли — шапку гасим сразу: открыв профиль снова, человек видит
           крупное имя, а не строку от прошлого раза. */
        if (!open) {
            reset();
            return undefined;
        }
        const scroller = findScrollParent(bar);
        const target = document.querySelector('.mobile-sheet-hero__name');
        const avatar = document.querySelector('.mobile-sheet-hero__avatar');
        const small = bar?.querySelector('.mobile-sheet-topbar__name');
        if (!bar || !scroller || !target || !small) {
            reset();
            return undefined;
        }

        let rowCenter = 0;
        let barBottom = 0;
        let nameCenter = 0;
        let endScale = 1;
        let sizeRatio = 1;
        let avatarTop = 0;
        let avatarHeight = 1;
        let padTop = 0;
        const measure = () => {
            padTop = parseFloat(getComputedStyle(bar).paddingTop) || 0;
            const heroSize = parseFloat(getComputedStyle(target).fontSize) || 21;
            endScale = ROW_FONT_SIZE / heroSize;
            sizeRatio = heroSize / (parseFloat(getComputedStyle(small).fontSize) || heroSize);
            avatarTop = avatar ? offsetWithin(avatar, scroller) : 0;
            avatarHeight = avatar ? Math.max(1, avatar.offsetHeight) : 1;
        };
        /* Положения — на каждом кадре и дробно. Целые offsetTop сдвигали копии
           на пиксель, а замер один раз при открытии попадал на приближение листа
           (97% → 100%) и ставил маленькое имя на 5 px выше крупного. Шапка не
           трансформируется, крупное имя масштабируется от своего центра — центр
           от этого не сдвигается, а на раскладку чтение не влияет. */
        const place = () => {
            const blockRect = scroller.getBoundingClientRect();
            const barRect = bar.getBoundingClientRect();
            barBottom = barRect.height;
            rowCenter = barRect.top - blockRect.top + padTop + (barRect.height - padTop) / 2;
            const nameRect = target.getBoundingClientRect();
            nameCenter = nameRect.top + nameRect.height / 2 - blockRect.top + scroller.scrollTop;
        };

        let frame = 0;
        const paint = () => {
            frame = 0;
            place();
            const y = scroller.scrollTop;
            /* Насколько крупное имя ещё ниже строки. */
            const below = nameCenter - y - rowCenter;
            const travel = clamp01(1 - below / NAME_TRAVEL);
            /* Крупное имя к концу пути ровно в размер строки. */
            const nameScale = 1 - (1 - endScale) * travel;
            const title = clamp01((HANDOFF_FROM - below) / HANDOFF_SPAN);
            const glass = clamp01((y - (avatarTop - barBottom)) / GLASS_RANGE);
            /* Портрет сжимается, пока уходит под шапку: от касания её нижней
               кромки до того, как скрылся целиком. */
            const avatarShrink = clamp01((barBottom - (avatarTop - y)) / avatarHeight);
            const { style } = scroller;
            style.setProperty('--sheet-title', title.toFixed(3));
            /* Маленькое имя — там же, где крупное, и того же размера, пока то не
               дошло до строки; дальше стоит в строке. */
            style.setProperty('--sheet-title-y', Math.max(0, below).toFixed(1));
            style.setProperty('--sheet-title-scale', (sizeRatio * nameScale).toFixed(4));
            style.setProperty('--sheet-name-scale', nameScale.toFixed(4));
            style.setProperty('--sheet-glass', glass.toFixed(3));
            style.setProperty('--sheet-avatar', avatarShrink.toFixed(3));
        };
        const schedule = () => {
            if (!frame) frame = requestAnimationFrame(paint);
        };
        const remeasure = () => {
            measure();
            schedule();
        };

        measure();
        paint();
        scroller.addEventListener('scroll', schedule, { passive: true });
        window.addEventListener('resize', remeasure);
        return () => {
            if (frame) cancelAnimationFrame(frame);
            scroller.removeEventListener('scroll', schedule);
            window.removeEventListener('resize', remeasure);
        };
    }, [open, name]);

    return (
        <div ref={barRef} className="mobile-sheet-topbar">
            <span className="mobile-sheet-topbar__glass" aria-hidden="true" />
            <span className="mobile-sheet-topbar__name">{name}</span>
        </div>
    );
}
