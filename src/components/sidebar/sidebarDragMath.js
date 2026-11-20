/* Геометрия перетаскивания разделов сайдбара — без DOM, чтобы её держал
 * node --test.
 *
 * Как на iPhone: поднятая строка едет за пальцем, остальные расступаются,
 * открывая щель там, куда она ляжет; подержать её над разделом — получится
 * папка, над папкой — ляжет в папку; отпустить заметно ниже последнего
 * раздела — откроется новая карточка. Строки считаются по РАСКЛАДКЕ БЕЗ
 * поднятой строки (дыра на её старом месте закрыта) — ctop.
 *
 * rows — видимые строки списка сверху вниз в координатах содержимого списка:
 *   { kind: 'item' | 'folder' | 'hr', entry, folder, id, open, block, fixed, top, height }
 *   у раздела в папке folder — id папки; у шапки папки id — её id, open — раскрыта ли.
 * unit — поднятое: { start, end } — номера строк (папка едет вместе со своими
 * разделами одним куском). */

import { folderRef } from './sidebarLayoutModel.js';

/* Сколько сдвинуть палец вбок, чтобы у нижнего края папки выбрать «в папку»
   или «за папку» явно. Без сдвига строка остаётся там, где была. */
export const NEST_SHIFT_PX = 14;

/* Верхняя и нижняя четверти строки — встать перед ней или за ней; середина —
   «на неё»: в папку, а у раздела — папка из двух (после выдержки). */
export const ZONE_EDGE = 0.25;

/* Воздух между карточками меню (черта-разделитель) — на таком расстоянии
   под последней строкой встаёт место новой карточки. */
export const BLOCK_GAP_PX = 10;

/* На сколько ниже щели в конце меню увести курсор, чтобы вместо «в конец
   последней карточки» открылась новая карточка, и сколько нужно вернуться
   назад, чтобы передумать (без запаса на границе место дрожало бы). */
export const NEW_BLOCK_REACH_PX = 14;
export const NEW_BLOCK_HYSTERESIS_PX = 10;

export const unitHeight = (rows, unit) => {
    let height = 0;
    for (let i = unit.start; i <= unit.end; i += 1) height += rows[i]?.height || 0;
    return height;
};

/** Строки без поднятого куска, каждая с ctop — где она стоит, когда дыра
 *  на месте поднятого закрыта. */
export const compactRows = (rows, unit) => {
    const out = [];
    let y = rows.length ? rows[0].top : 0;
    rows.forEach((row, index) => {
        if (index >= unit.start && index <= unit.end) return;
        out.push({ ...row, index, ctop: y });
        y += row.height;
    });
    return out;
};

/** Низ меню без поднятого — отсюда начинается щель «в конец». */
export const compactBottom = (compact, rows = []) => {
    const last = compact[compact.length - 1];
    if (last) return last.ctop + last.height;
    return rows.length ? rows[0].top : 0;
};

/** Что под КУРСОРОМ (pointerY — в координатах содержимого списка) при щели
 *  shownGap, по которой строки расставлены на экране сейчас (null — щели
 *  нет). Возвращает { gap, into, center, newBlock }:
 *    gap — щель, куда встанет поднятое (null — щели нет);
 *    into — id папки, в которую кладут (курсор над серединой её шапки);
 *    center — номер раздела верхнего уровня, над серединой которого курсор:
 *      подержать — и выйдет папка из двух (выдержку считает список);
 *    newBlock — курсор заметно ниже последней строки: новая карточка.
 *
 *  ПО КУРСОРУ, А НЕ ПО КРАЮ ПОДНЯТОЙ СТРОКИ. Строки расступаются от курсора: у
 *  верхней четверти строки щель встаёт перед ней, у нижней — за ней, а над
 *  серединой щель ЗАМИРАЕТ — иначе до середины соседа (папка, «положить на
 *  раздел») было бы не дотянуться: он уезжал бы из-под курсора. Замирает
 *  только щель, стоящая вплотную к строке; курсор, перескочивший далеко,
 *  сначала подводит щель к строке по половинам — так бросок никогда не
 *  уходит в щель, которой не видно рядом с курсором. С панели (щели ещё нет)
 *  над серединой раздела щель открывается по половинам: папку из раздела
 *  делают, подержав его, когда он уже расступился. */
export const pointerZone = (compact, {
    pointerY,
    shownGap = null,
    height,
    dragged,
    wasNewBlock = false,
}) => {
    const plain = (gap) => ({ gap, into: null, center: null, newBlock: false });
    if (!compact.length) return plain(0);
    const isItem = dragged?.kind === 'item';
    const endTop = compactBottom(compact);
    // Щель — пустое место строки: курсор в ней ничего не меняет.
    if (shownGap !== null) {
        const slotTop = shownGap < compact.length ? compact[shownGap].ctop : endTop;
        if (pointerY >= slotTop && pointerY < slotTop + height) return plain(shownGap);
    }
    for (let i = 0; i < compact.length; i += 1) {
        const row = compact[i];
        const top = row.ctop + (shownGap !== null && i >= shownGap ? height : 0);
        if (pointerY < top) return plain(i);
        if (pointerY >= top + row.height) continue;
        const f = (pointerY - top) / row.height;
        // Черта между карточками: верхняя половина — в конец карточки над ней, нижняя — в начало следующей.
        if (row.kind === 'hr') return plain(f < 0.5 ? i : i + 1);
        if (f < ZONE_EDGE) return plain(i);
        if (f > 1 - ZONE_EDGE) return plain(i + 1);
        const halves = f < 0.5 ? i : i + 1;
        const adjacent = shownGap === null || shownGap === i || shownGap === i + 1;
        if (!adjacent) return plain(halves);
        if (isItem && row.kind === 'folder') return { gap: shownGap, into: row.id, center: null, newBlock: false };
        if (shownGap === null) return plain(halves);
        if (isItem && row.kind === 'item' && !row.folder && !row.fixed) {
            return { gap: shownGap, into: null, center: i, newBlock: false };
        }
        return plain(shownGap);
    }
    // Ниже всех строк: у самого низа — в конец последней карточки, дальше — новая карточка.
    const reach = endTop + height + NEW_BLOCK_REACH_PX - (wasNewBlock ? NEW_BLOCK_HYSTERESIS_PX : 0);
    if (pointerY >= reach) return { gap: null, into: null, center: null, newBlock: true };
    return plain(compact.length);
};

const memberOf = (row, folderId) => Boolean(row && row.kind === 'item' && row.folder && row.folder === folderId);

/* Папку не кладут в папку: щель внутри чужой раскрытой папки уходит к
   ближнему краю этой папки — перед шапкой или за последним разделом. */
export const snapGapForFolder = (compact, gap) => {
    const next = compact[gap];
    const prev = compact[gap - 1];
    const folderId = next?.kind === 'item' && next.folder ? next.folder : null;
    if (!folderId || !(memberOf(prev, folderId) || (prev?.kind === 'folder' && prev.id === folderId))) return gap;
    let head = gap - 1;
    while (head >= 0 && !(compact[head].kind === 'folder' && compact[head].id === folderId)) head -= 1;
    let tail = gap;
    while (tail < compact.length && memberOf(compact[tail], folderId)) tail += 1;
    if (head < 0) return tail;
    return gap - head <= tail - gap ? head : tail;
};

/** Куда ляжет поднятое при щели gap: { block, folder, before } в терминах
 *  раскладки (before — запись, перед которой встать; null — в конец).
 *  dragged: { kind, folder, block } — что подняли и откуда; shiftX — сдвиг
 *  пальца вбок от точки, где строку взяли. */
export const dropTarget = (compact, gap, dragged, shiftX = 0) => {
    const prev = compact[gap - 1] || null;
    const next = compact[gap] || null;
    const isItem = dragged.kind === 'item';
    // Между разделами папки (или сразу под её шапкой) — в папку.
    if (next && next.kind === 'item' && next.folder && isItem) {
        return { block: next.block, folder: next.folder, before: next.entry };
    }
    /* Под последним разделом раскрытой папки (или под раскрытой пустой) —
       место двоякое: конец папки или строка за ней. Решает сдвиг пальца
       вбок, а без него — откуда строку взяли: свой раздел папки остаётся в
       ней, чужой встаёт за папкой. */
    const tailOf = prev && prev.kind === 'item' && prev.folder
        ? prev.folder
        : (prev && prev.kind === 'folder' && prev.open ? prev.id : null);
    /* «В конец» — это сразу за последней ВИДИМОЙ строкой (after), а не в конец
       массива раскладки: за ней бывают спрятанные сейчас разделы. */
    if (tailOf && isItem) {
        const inside = shiftX >= NEST_SHIFT_PX
            ? true
            : (shiftX <= -NEST_SHIFT_PX ? false : dragged.folder === tailOf);
        if (inside) {
            const after = prev.kind === 'item' && prev.folder === tailOf ? prev.entry : null;
            return { block: prev.block, folder: tailOf, before: null, after };
        }
    }
    if (next && next.kind !== 'hr' && !(next.kind === 'item' && next.folder)) {
        return { block: next.block, folder: null, before: next.entry };
    }
    if (prev && prev.kind !== 'hr') {
        // Раздел папки на верхнем уровне представлен самой папкой.
        const after = prev.kind === 'item' && prev.folder ? folderRef(prev.folder) : prev.entry;
        return { block: prev.block, folder: null, before: null, after };
    }
    if (next && next.kind !== 'hr') return { block: next.block, folder: null, before: next.entry };
    return { block: dragged.block ?? 0, folder: null, before: null };
};

/** Где встанет верх поднятого куска при щели gap. */
export const gapTop = (compact, gap, rows) => (gap < compact.length ? compact[gap].ctop : compactBottom(compact, rows));

/** На сколько сдвинуть каждую оставшуюся строку: index строки → dy.
 *  gap === null — щели нет (раздел кладут в папку): дыра просто закрывается. */
export const rowShifts = (rows, compact, gap, height) => {
    const shifts = new Map();
    compact.forEach((row, i) => {
        const virtual = row.ctop + (gap !== null && i >= gap ? height : 0);
        shifts.set(row.index, virtual - rows[row.index].top);
    });
    return shifts;
};

/** Шаг клавиатурой: щель на step строк выше (step < 0) или ниже текущего
 *  места. Шаг больше единицы нужен, когда ближнее место совпадает с текущим
 *  (папку не пускают внутрь раскрытой папки, и её щель отскакивает назад). */
export const keyboardGap = (compact, unit, step) => (
    Math.max(0, Math.min(compact.length, unit.start + step))
);
