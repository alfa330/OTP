/*
 * «Альбом» 4 You: раскладка книги без DOM — её грузит Node в
 * tests/four_you_album_layout.test.mjs.
 *
 * КНИГА. На широком экране — разворот: слева форзац (изнанка обложки), справа
 * первое фото, дальше по два фото на разворот; если фото не хватает на пару,
 * последней правой страницей встаёт задний форзац. На узком экране (телефон,
 * узкое окно) — одна страница: фото за фото, без форзацев.
 *
 * «Место» книги — номер разворота или номер страницы. Ход листа описывает
 * turnPlan теми же ролями, что и ридер «Библиотеки» (readerEngine.js:
 * planForward/planBackward): лист, страница под ним, страница на месте и
 * оборот листа.
 *
 * ФОТО НА СТРАНИЦЕ — в рамке 470×630, как карточка ленты и холст редактора:
 * разметка (рисунок, стикеры, текст) хранится в долях этой рамки и ложится
 * туда же, где её нарисовали, только в рамке тех же пропорций.
 */

export const PAGE_RATIO = 0.7;               // ширина страницы к высоте
export const PHOTO_ASPECT = 470 / 630;       // рамка фото = карточка ленты
export const MIN_SPREAD_PAGE = 280;          // уже — разворот не читается
export const MAX_PAGE_WIDTH = 560;

/* Книга под сцену: разворот, если две страницы от 280 точек влезают по
   ширине; иначе одна страница посередине. Координаты — от угла сцены. */
export const albumMetrics = (stageWidth, stageHeight) => {
    const width = Math.max(0, Math.floor(Number(stageWidth) || 0));
    const height = Math.max(0, Math.floor(Number(stageHeight) || 0));
    const spreadPage = Math.min(Math.floor(height * PAGE_RATIO), Math.floor((width - 40) / 2), MAX_PAGE_WIDTH);
    if (spreadPage >= MIN_SPREAD_PAGE) {
        const pageHeight = Math.round(spreadPage / PAGE_RATIO);
        return {
            mode: 'spread',
            pageWidth: spreadPage,
            height: pageHeight,
            width: spreadPage * 2,
            left: Math.round((width - spreadPage * 2) / 2),
            top: Math.max(0, Math.round((height - pageHeight) / 2)),
        };
    }
    // Нижняя граница 120 — но не выше сцены: на низком телефоне боком книга
    // иначе заходила под полосу листания.
    const byHeight = Math.floor(height * PAGE_RATIO);
    const pageWidth = Math.max(Math.min(120, byHeight), Math.min(byHeight, width - 24, MAX_PAGE_WIDTH));
    const pageHeight = Math.min(height, Math.round(pageWidth / PAGE_RATIO));
    return {
        mode: 'single',
        pageWidth,
        height: pageHeight,
        width: pageWidth,
        left: Math.round((width - pageWidth) / 2),
        top: Math.max(0, Math.round((height - pageHeight) / 2)),
    };
};

/* Полоса номера страницы внизу: номер стоит в ней, подпись в неё не заходит. */
export const folioBand = (pageHeight) => Math.round(pageHeight * 0.04) + 14;

/* Где на странице рамка фото и подпись под ней. Поля — доли ширины страницы:
   у маленькой страницы поля в пикселях съели бы фото. */
export const pageLayout = (pageWidth, pageHeight) => {
    const side = Math.round(pageWidth * 0.08);
    const top = Math.round(pageWidth * 0.08);
    const captionMin = Math.round(pageWidth * 0.15);
    let photoWidth = Math.max(0, pageWidth - side * 2);
    let photoHeight = Math.round(photoWidth / PHOTO_ASPECT);
    const room = Math.max(0, pageHeight - top - captionMin);
    if (photoHeight > room) {
        photoHeight = room;
        photoWidth = Math.round(photoHeight * PHOTO_ASPECT);
    }
    const left = Math.round((pageWidth - photoWidth) / 2);
    const captionTop = top + photoHeight;
    return {
        photo: { left, top, width: photoWidth, height: photoHeight },
        caption: {
            left,
            top: captionTop,
            width: photoWidth,
            height: Math.max(0, pageHeight - captionTop - folioBand(pageHeight)),
        },
    };
};

/* Страницы разворотов по порядку: форзац, фото…, задний форзац для пары. */
export const spreadPages = (count) => {
    const pages = [{ kind: 'front' }];
    for (let index = 0; index < count; index += 1) pages.push({ kind: 'photo', index });
    if (pages.length % 2) pages.push({ kind: 'back' });
    return pages;
};

/* Сколько мест у книги: разворотов или страниц. */
export const placeCount = (mode, count) => {
    const total = Math.max(0, Number(count) || 0);
    if (!total) return 0;
    return mode === 'spread' ? Math.ceil((total + 1) / 2) : total;
};

export const clampPlace = (mode, place, count) => {
    const total = placeCount(mode, count);
    if (!total) return 0;
    return Math.max(0, Math.min(total - 1, Math.floor(Number(place) || 0)));
};

/* Место, где видно фото index. При смене режима (окно сузили или растянули)
   книга остаётся на том же фото. */
export const placeOfPhoto = (mode, index) => {
    const photo = Math.max(0, Math.floor(Number(index) || 0));
    return mode === 'spread' ? Math.floor((photo + 1) / 2) : photo;
};

/* Первое фото места. */
export const photoOfPlace = (mode, place) => {
    const at = Math.max(0, Math.floor(Number(place) || 0));
    return mode === 'spread' ? Math.max(0, at * 2 - 1) : at;
};

/* Страницы, лежащие на месте: на развороте [левая, правая], на одной — [фото]. */
export const placePages = (mode, place, count) => {
    if (!placeCount(mode, count)) return [];
    const at = clampPlace(mode, place, count);
    if (mode === 'spread') {
        const pages = spreadPages(count);
        return [pages[at * 2], pages[at * 2 + 1]];
    }
    return [{ kind: 'photo', index: at }];
};

/*
 * План хода с места place: какие страницы где лежат, пока лист в движении.
 *   leaf  — лист, который переворачивается (обрезается по сгибу);
 *   under — страница, что открывается под ним;
 *   still — страница, что лежит на месте (только разворот);
 *   flap  — оборот листа: на развороте — следующая левая страница, на одной
 *           странице — то же фото, просвечивающее сквозь бумагу.
 * Ход назад — тот же ход от прошлого места к текущему, от конца к началу.
 * null — листать некуда.
 */
export const turnPlan = (mode, place, forward, count) => {
    const total = placeCount(mode, count);
    const target = forward ? place + 1 : place - 1;
    if (!total || place < 0 || place >= total || target < 0 || target >= total) return null;
    if (mode === 'spread') {
        const pages = spreadPages(count);
        const base = 2 * place;
        return forward
            ? { target, still: pages[base], leaf: pages[base + 1], flap: pages[base + 2], under: pages[base + 3] }
            : { target, still: pages[base - 2], leaf: pages[base - 1], flap: pages[base], under: pages[base + 1] };
    }
    const photo = (index) => ({ kind: 'photo', index });
    return forward
        ? { target, still: null, leaf: photo(place), flap: photo(place), under: photo(place + 1) }
        : { target, still: null, leaf: photo(place - 1), flap: photo(place - 1), under: photo(place) };
};

/* Страницы, которые держим готовыми: место и по два соседних в каждую сторону.
   Ход начинается сразу, без ожидания загрузки, — всё, что назовёт turnPlan,
   уже на столе. Второй сосед нужен быстрому второму нажатию: прошлый ход
   досчитывается мгновенно, место уже новое, а React ещё не перерисовал. */
export const windowPages = (mode, place, count) => {
    if (!placeCount(mode, count)) return [];
    const at = clampPlace(mode, place, count);
    if (mode === 'spread') {
        const pages = spreadPages(count);
        return pages.slice(Math.max(0, 2 * at - 4), Math.min(pages.length, 2 * at + 6));
    }
    const out = [];
    for (let index = Math.max(0, at - 2); index <= Math.min(count - 1, at + 2); index += 1) {
        out.push({ kind: 'photo', index });
    }
    return out;
};

/* Сторона страницы в книге — от неё тень корешка и место номера. На
   развороте чётные страницы последовательности левые, нечётные — правые. */
export const pageSide = (mode, page) => {
    if (mode !== 'spread' || !page) return 'single';
    if (page.kind === 'front') return 'left';
    if (page.kind === 'back') return 'right';
    return (page.index + 1) % 2 === 0 ? 'left' : 'right';
};

const backgroundOf = (images, index) => {
    const value = images?.[index]?.annotations?.background;
    return typeof value === 'string' && value && value !== 'none' ? value : null;
};

/* Фон сцены у места — фон, выбранный у фото в разметке. На развороте правое
   фото главнее (его только что открыли), без своего фона — левое. */
export const placeBackground = (mode, place, images) => {
    const list = Array.isArray(images) ? images : [];
    if (!placeCount(mode, list.length)) return 'none';
    const at = clampPlace(mode, place, list.length);
    if (mode === 'spread') return backgroundOf(list, at * 2) || backgroundOf(list, at * 2 - 1) || 'none';
    return backgroundOf(list, at) || 'none';
};

/* «2–3 из 18», «1 из 18»: номера фото на месте. */
export const placeLabel = (mode, place, count) => {
    const total = Math.max(0, Number(count) || 0);
    if (!total) return '';
    const at = clampPlace(mode, place, total);
    if (mode === 'spread') {
        const first = Math.max(0, at * 2 - 1);
        const last = Math.min(total - 1, at * 2);
        return first === last ? `${first + 1} из ${total}` : `${first + 1}–${last + 1} из ${total}`;
    }
    return `${at + 1} из ${total}`;
};

/* Толщина стопок по краям разворота — по доле пролистанного. */
export const stackWidths = (place, total) => {
    const share = total > 1 ? Math.max(0, Math.min(1, place / (total - 1))) : 0;
    return { left: Math.round(2 + 6 * share), right: Math.round(2 + 6 * (1 - share)) };
};
