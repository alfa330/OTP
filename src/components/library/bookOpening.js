/*
 * Раскрытие книги «Библиотеки» (задача #282): ключевые кадры, без DOM.
 *
 * ХОРЕОГРАФИЯ.
 *   1. Полёт: обложка из карточки каталога летит на место закрытой книги (на
 *      развороте — на место правой страницы, у книги справа срез страниц),
 *      фон ридера проявляется под ней.
 *   2. Ожидание: книга ещё распаковывается — закрытая книга лежит на столе.
 *      В начале книги она так и остаётся лежать, пока её не откроют: обложка
 *      — не страница, и показывать её листом «1» значило бы читать её дважды.
 *      На развороте закрытая книга лежит ПО ЦЕНТРУ стола (shift), а не на
 *      месте правой страницы: так лежит закрытая книга, а не половина открытой.
 *   3. Раскрытие: обложка поворачивается вокруг корешка. На развороте тот же
 *      лист проходит ребром над корешком и ложится налево уже левой страницей —
 *      это сама страница движка, её поворот продолжает поворот обложки (одна
 *      перспектива, одна кривая времени). Под поднятой обложкой — тень, у
 *      ложащегося листа — свет, тень книги на столе растёт вслед за листом.
 *      На одной странице (телефон, узкое окно) обложка распахивается влево,
 *      как дверца, и уходит.
 *   4. Закрытие — те же кадры в обратную сторону, потом полёт в карточку (или
 *      книга остаётся лежать закрытой — пролистали назад с первой страницы).
 *
 * ВСЁ — transform и opacity через Web Animations: такие анимации идут в потоке
 * композитора, и распаковка книги, вёрстка главы или подсчёт страниц в это
 * время кадры не рвут. Прошлая версия анимировала left/top/width/height —
 * вёрстку на каждом кадре — и дёргалась ровно тогда, когда книга грузилась.
 *
 * ПОЛЁТ БЕЗ ИСКАЖЕНИЯ. Карточка — 2:3, страница — 0.7, экран телефона — 0.5.
 * Растянуть картинку transform'ом значит сплющить обложку. Поэтому рамка
 * летит масштабом по двум осям, а картинка внутри получает обратный масштаб:
 * в каждый момент она заполняет текущую рамку «cover», как в карточке.
 */

export const OPENING_MS = {
    fadeIn: 260,
    flyIn: 520,
    hingeSpread: 900,
    hingeSingle: 640,
    closeSpread: 580,
    closeSingle: 420,
    flyOut: 440,
    fadeOut: 300,
    coverFade: 220,
    // Страницы под почти закрытой обложкой тают за это время (не пропадают
    // разом): остаток хода закрытия после старта полёта — около 120 мс.
    pagesFade: 140,
};

/* Кривые: полёт — быстрый старт и мягкая посадка (как у системных окон iOS);
   раскрытие — обложку сначала приподнимают, лист ускоряется и мягко ложится;
   закрытие симметрично, чтобы обратный ход выглядел тем же движением. */
export const OPENING_EASING = {
    fadeIn: 'cubic-bezier(0.25, 0.1, 0.25, 1)',
    flyIn: 'cubic-bezier(0.2, 0.85, 0.25, 1)',
    hinge: 'cubic-bezier(0.5, 0.04, 0.32, 1)',
    close: 'cubic-bezier(0.45, 0.05, 0.35, 1)',
    flyOut: 'cubic-bezier(0.4, 0.05, 0.15, 1)',
};

/* Полёт обратно в карточку стартует, пока обложка доходит последние градусы:
   иначе закрытая книга «стояла» бы между захлопыванием и полётом. */
export const CLOSE_OVERLAP = 0.8;

const round = (value) => Number(value.toFixed(4));
const lerp = (a, b, t) => a + (b - a) * t;

/* Перспектива — от ширины страницы: при повороте на 45° свободный край
   ближе к глазу и выглядит крупнее на ~14 %. Сильнее — обложка «распахивается
   на зрителя», как дверь; слабее — плоско. */
export const perspectiveFor = (pageWidth) => Math.round(Math.max(1400, pageWidth * 5.5));

/* Рамка картинки внутри box, заполняющая его целиком (object-fit: cover),
   по центру. aspect — ширина/высота картинки. */
export const coverBox = (aspect, box) => {
    const ratio = aspect > 0 ? aspect : 2 / 3;
    const width = ratio > box.width / box.height ? box.height * ratio : box.width;
    const height = width / ratio;
    return { width, height, left: (box.width - width) / 2, top: (box.height - height) / 2 };
};

/* Во сколько раз картинка при «cover» в рамке box больше, чем при единичной
   высоте. Нужно только отношение двух таких чисел. */
const coverScale = (aspect, box) => Math.max(box.width / aspect, box.height);

/*
 * Полёт рамки из from в to (прямоугольники экрана: left, top, width, height).
 * Рамка стоит в to и летит transform'ом с началом в левом верхнем углу:
 * сдвиг и масштаб линейны по ходу, поэтому ей хватает двух кадров. Картинке —
 * обратный масштаб, он по ходу нелинеен: кадры через шаг 1/steps на той же
 * кривой времени (смещения кадров — в пространстве хода, как и у рамки).
 */
export const flightFrames = (from, to, aspect, steps = 16) => {
    const at = (t) => ({
        left: lerp(from.left, to.left, t),
        top: lerp(from.top, to.top, t),
        width: lerp(from.width, to.width, t),
        height: lerp(from.height, to.height, t),
    });
    const frame = (t) => {
        const r = at(t);
        return `translate(${round(r.left - to.left)}px, ${round(r.top - to.top)}px) scale(${round(r.width / to.width)}, ${round(r.height / to.height)})`;
    };
    const base = coverScale(aspect, to);
    const art = [];
    const anchor = [];
    for (let i = 0; i <= steps; i += 1) {
        const t = i / steps;
        const r = at(t);
        const sx = r.width / to.width;
        const sy = r.height / to.height;
        const k = coverScale(aspect, r) / base;
        art.push({ offset: round(t), transform: `scale(${round(k / sx)}, ${round(k / sy)})` });
        // Значок и название заглушки — у своих углов, в масштабе ширины рамки.
        anchor.push({ offset: round(t), transform: `scale(1, ${round(sx / sy)})` });
    }
    return { frame: [{ transform: frame(0) }, { transform: frame(1) }], art, anchor };
};

/* Кадры в обратную сторону — для закрытия теми же движениями. */
export const reverseFrames = (frames) => frames
    .map((frame, index) => ({ ...frame, offset: frame.offset ?? index / (frames.length - 1) }))
    .map((frame) => ({ ...frame, offset: round(1 - frame.offset) }))
    .reverse();

/*
 * Раскрытие. Всё — в пространстве хода 0..1 с одной кривой времени.
 *
 * Разворот (spread): лист поворачивается на 180° вокруг корешка. До середины
 * хода видна обложка (0 → −90°), после — левая страница движка (90° → 0°).
 * Перспектива у обеих — с точки над корешком (transform-origin у корешка),
 * поэтому в середине хода обе вырождаются в одну и ту же линию над ним.
 *
 * Одна страница (single): обложка распахивается влево, взгляд — из середины
 * страницы; к ребру (90°) она уже растаяла — на телефоне дальше край экрана,
 * на компьютере изнанка легла бы на стол серым пятном.
 *
 * shift (только разворот): насколько закрытая книга лежит левее правой
 * страницы — половина ширины страницы, когда она по центру стола. До середины
 * хода, пока обложка встаёт ребром над корешком, она съезжает на место правой
 * страницы, а раскрытая книга (pan — сдвиг всего ридера) — следом, тем же
 * движением: в середине хода корешок уже на месте, и лист обложки сменяется
 * левой страницей ровно над ним.
 */
export const hingeFrames = ({ spread, pageWidth, shift = 0 }) => {
    const p = perspectiveFor(pageWidth);
    const cover = spread
        ? (deg) => `perspective(${p}px) rotateY(${deg}deg)`
        // Поворот вокруг левого края, а точка взгляда — середина страницы.
        : (deg) => `translateX(${round(pageWidth / 2)}px) perspective(${p}px) translateX(${round(-pageWidth / 2)}px) rotateY(${deg}deg)`;

    if (!spread) {
        return {
            // Тает до ребра: дальше 90° видна изнанка, серым «призраком» на столе.
            cover: [
                { offset: 0, transform: cover(0), opacity: 1 },
                { offset: 0.66, transform: cover(-66), opacity: 1 },
                { offset: 0.9, transform: cover(-90), opacity: 0 },
                { offset: 1, transform: cover(-100), opacity: 0 },
            ],
            // Свет на обложке: чем круче она к столу, тем темнее.
            coverLight: [
                { offset: 0, opacity: 0 },
                { offset: 0.9, opacity: 0.42 },
                { offset: 1, opacity: 0.42 },
            ],
            // Тень поднятой обложки на странице — у корешка, уходит к нему.
            cast: [
                { offset: 0, opacity: 0, transform: 'scaleX(1)' },
                { offset: 0.22, opacity: 1, transform: 'scaleX(0.72)' },
                { offset: 0.9, opacity: 0.25, transform: 'scaleX(0.08)' },
                { offset: 1, opacity: 0, transform: 'scaleX(0.04)' },
            ],
        };
    }

    const leaf = (deg) => `perspective(${p}px) rotateY(${deg}deg)`;
    const slide = (offset) => round(shift * Math.min(1, offset / 0.5));
    // Сдвиг — первой функцией у каждого кадра: списки функций у соседних кадров
    // обязаны совпадать, иначе браузер не интерполирует их по частям.
    const moved = (offset, transform) => (shift ? `translateX(${slide(offset)}px) ${transform}` : transform);
    // Тень книги на столе идёт за следом листа: след ≈ cos угла от стола.
    const footprint = [0.5, 0.6, 0.7, 0.8, 0.9, 1].map((offset) => {
        const angle = Math.PI * (1 - offset);            // 90° → 0° в радианах
        return { offset, transform: `scaleX(${round(0.5 + 0.5 * Math.cos(angle))})` };
    });
    return {
        cover: [
            { offset: 0, transform: moved(0, cover(0)), opacity: 1 },
            { offset: 0.5, transform: moved(0.5, cover(-90)), opacity: 1 },
            { offset: 0.5, transform: moved(0.5, cover(-90)), opacity: 0 },
            { offset: 1, transform: moved(1, cover(-180)), opacity: 0 },
        ],
        coverLight: [
            { offset: 0, opacity: 0 },
            { offset: 0.5, opacity: 0.45 },
            { offset: 1, opacity: 0.45 },
        ],
        cast: [
            { offset: 0, opacity: 0, transform: moved(0, 'scaleX(1)') },
            { offset: 0.14, opacity: 1, transform: moved(0.14, 'scaleX(0.78)') },
            { offset: 0.5, opacity: 0.3, transform: moved(0.5, 'scaleX(0.08)') },
            { offset: 0.6, opacity: 0, transform: moved(0.6, 'scaleX(0.04)') },
            { offset: 1, opacity: 0, transform: moved(1, 'scaleX(0.04)') },
        ],
        // Весь ридер (страницы движка) — тем же сдвигом: правая страница всё
        // время ровно под обложкой. Без shift двигать нечего — и ключа нет.
        ...(shift ? {
            pan: [
                { offset: 0, transform: `translateX(${round(-shift)}px)` },
                { offset: 0.5, transform: 'translateX(0px)' },
                { offset: 1, transform: 'translateX(0px)' },
            ],
        } : {}),
        leaf: [
            { offset: 0, transform: leaf(180), opacity: 0 },
            { offset: 0.5, transform: leaf(90), opacity: 0 },
            { offset: 0.5, transform: leaf(90), opacity: 1 },
            { offset: 1, transform: leaf(0), opacity: 1 },
        ],
        leafShade: [
            { offset: 0, transform: leaf(180), opacity: 0 },
            { offset: 0.5, transform: leaf(90), opacity: 0 },
            { offset: 0.5, transform: leaf(90), opacity: 0.55 },
            { offset: 1, transform: leaf(0), opacity: 0 },
        ],
        bookShadow: [{ offset: 0, transform: 'scaleX(0.5)' }, ...footprint],
        // Стопка прочитанных страниц слева появляется, когда лист лёг.
        edgeLeft: [{ offset: 0, opacity: 0 }, { offset: 0.88, opacity: 0 }, { offset: 1, opacity: 1 }],
    };
};
