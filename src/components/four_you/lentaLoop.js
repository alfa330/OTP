/*
 * Круг ленты 4 You: без DOM — его грузит Node в tests/four_you_lenta_loop.test.mjs.
 *
 * Лента идёт по кругу, только когда карточек хотя бы вдвое больше, чем видно
 * в половине экрана: тогда шов круга (где последняя карточка сменяется
 * первой) всегда за краем. На широком мониторе для этого нужно 22 карточки,
 * и 18 фото не хватало — лента упиралась в край. Поэтому фото, которых
 * меньше, лента повторяет копиями: карточка — это «слот», а не фото.
 */

export const CARD_STEP_X = 160;     // PARAMS.dirX ленты: сдвиг соседних карточек
export const CARD_HALF_WIDTH = 470; // запас на ширину самой карточки

/* Сколько карточек рисуется по одну сторону от середины (дальше — display: none). */
export const cullFor = (viewportWidth) => (
    Math.ceil(((Number(viewportWidth) || 0) * 0.5 + CARD_HALF_WIDTH) / CARD_STEP_X) + 2
);

/* Сколько копий фото нужно, чтобы лента шла по кругу без видимого шва.
   Одно фото по кругу не гоняем. */
export const copiesFor = (count, viewportWidth) => {
    const photos = Math.max(0, Math.floor(Number(count) || 0));
    if (photos < 2) return 1;
    return Math.max(1, Math.ceil((2 * cullFor(viewportWidth)) / photos));
};

/* Карточки ленты: все фото по порядку, столько раз, сколько копий. Ключ
   копии отличается от ключа фото — React держит у каждой свою карточку. */
export const buildCards = (images, copies) => {
    const list = [];
    const times = Math.max(1, Math.floor(Number(copies) || 1));
    for (let copy = 0; copy < times; copy += 1) {
        (images || []).forEach((image, photo) => {
            list.push({ image, photo, key: copy ? `${image.id}~${copy}` : String(image.id) });
        });
    }
    return list;
};

/* Доля пройденного круга для полоски внизу — по фото, а не по копиям:
   иначе при двух копиях полоска пробегала бы круг дважды. */
export const loopProgress = (position, photos) => {
    const count = Math.max(1, Math.floor(Number(photos) || 1));
    const at = ((Number(position) || 0) % count + count) % count;
    return (at / count) * 100;
};
