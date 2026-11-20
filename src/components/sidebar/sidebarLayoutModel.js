/* Личная раскладка разделов сайдбара: свой порядок и папки (просьба владельца
 * 20.11.2026: «кнопка ручки — перетаскивать разделы кому как удобно и
 * сохранить, ⌘Z в режиме правки, раскрывашки с названием, всё как в iOS»).
 *
 * Здесь только данные и правила — без React и без DOM, чтобы их держал
 * node --test. Меню собирают ролевые ветки App.jsx (их сверяют ~30 тестов как
 * текст), поэтому раскладка не переписывает ветки, а накладывается поверх уже
 * собранного списка: ключ раздела — его подпись в меню.
 *
 * Раскладка:
 *   { v: 1,
 *     blocks:  [[ключ | 'folder:<id>', …], …],   // карточки меню сверху вниз
 *     folders: { <id>: { name, items: [ключ, …] } } }
 *
 * Карточки — те же блоки, что делят меню чертой: раздел можно перенести в
 * другую карточку или бросить ниже последнего раздела — тогда он открывает
 * новую карточку (newBlock). Папка появляется, как на iPhone, когда раздел
 * кладут на раздел (stackEntries), и исчезает, когда из неё унесли последний
 * (pruneEmptyFolders). Ключи разделов,
 * которых сейчас нет в меню (выбран отдел в селекторе, доступ сняли на время),
 * в раскладке ОСТАЮТСЯ: вернулся раздел — он на своём месте. Раздел, которого
 * раскладка не знает (новый в портале), встаёт рядом с соседом по
 * стандартному меню, а целиком новая карточка — после той, где лежит её
 * стандартный предшественник. */

export const LAYOUT_VERSION = 1;
export const FOLDER_PREFIX = 'folder:';
export const FOLDER_NAME_MAX = 40;
export const KEY_MAX = 80;
export const MAX_FOLDERS = 40;
export const MAX_BLOCKS = 60;
export const MAX_ENTRIES = 600;
export const DEFAULT_FOLDER_NAME = 'Новая папка';
const FOLDER_ID_RE = /^f[a-z0-9]{1,15}$/;

export const folderRef = (id) => `${FOLDER_PREFIX}${id}`;
export const isFolderRef = (entry) => typeof entry === 'string' && entry.startsWith(FOLDER_PREFIX);
export const folderIdOf = (entry) => (isFolderRef(entry) ? entry.slice(FOLDER_PREFIX.length) : null);
export const isFolderId = (id) => typeof id === 'string' && FOLDER_ID_RE.test(id);

/* Символ, который чистка превращает в пробел: управляющие C0 и C1 (среди C1 —
   NEL 0x85: Python считает его пробелом, а JS — нет), BOM 0xFEFF (наоборот:
   пробел для JS, но не для Python) и одиночная половинка суррогатной пары
   (её не закодировать в UTF-8 — база такую строку не примет). Список один и
   тот же у сервера (sidebar_layout/layout.py, clean_text): иначе раскладку,
   которую клиент считает правильной, сервер отвергал бы, и «Готово» молча не
   сохранялось бы. Сверку держит tests/test_sidebar_layout.py. */
const isBlankCode = (code) => (
    code < 0x20
    || (code >= 0x7f && code <= 0x9f)
    || code === 0xfeff
    || (code >= 0xd800 && code <= 0xdfff)
);

/* Пробелы — схлопнуть, длина — по символам, а не по UTF-16: сервер считает
   длину так же (len в Python), и обрезка посреди эмодзи дала бы строку,
   которую он отвергнет. */
export const cleanText = (value, max) => {
    let out = '';
    for (const ch of String(value ?? '')) {
        out += isBlankCode(ch.codePointAt(0)) ? ' ' : ch;
    }
    return Array.from(out.replace(/\s+/g, ' ').trim()).slice(0, max).join('').trim();
};

export const cleanFolderName = (value) => cleanText(value, FOLDER_NAME_MAX);

export const isItemKey = (key) => (
    typeof key === 'string'
    && key.length > 0
    && !key.startsWith(FOLDER_PREFIX)
    && cleanText(key, KEY_MAX) === key
);

export const emptyLayout = () => ({ v: LAYOUT_VERSION, blocks: [], folders: {} });

export const cloneLayout = (layout) => ({
    v: LAYOUT_VERSION,
    blocks: (layout?.blocks || []).map((block) => [...block]),
    folders: Object.fromEntries(Object.entries(layout?.folders || {}).map(([id, folder]) => [
        id, { name: folder.name, items: [...folder.items] },
    ])),
});

/* Раскладка из хранилища или с сервера — чужие данные: всё, что не по форме,
   отбрасываем молча (дубль ключа, папка без ссылки, лишнее сверх пределов), а
   не роняем меню. Пустые карточки не храним — их не видно и в них не бросить. */
export const normalizeLayout = (raw) => {
    if (!raw || typeof raw !== 'object' || raw.v !== LAYOUT_VERSION || !Array.isArray(raw.blocks)) return null;
    const rawFolders = raw.folders && typeof raw.folders === 'object' && !Array.isArray(raw.folders)
        ? raw.folders
        : {};
    const seen = new Set();
    const folders = {};
    const blocks = [];
    let total = 0;
    for (const rawBlock of raw.blocks.slice(0, MAX_BLOCKS)) {
        if (!Array.isArray(rawBlock)) continue;
        const block = [];
        for (const entry of rawBlock) {
            if (total >= MAX_ENTRIES) break;
            if (isFolderRef(entry)) {
                const id = folderIdOf(entry);
                const folder = Object.prototype.hasOwnProperty.call(rawFolders, id) ? rawFolders[id] : null;
                if (!isFolderId(id) || folders[id] || !folder || typeof folder !== 'object') continue;
                if (Object.keys(folders).length >= MAX_FOLDERS) continue;
                const items = [];
                total += 1;
                for (const key of Array.isArray(folder.items) ? folder.items : []) {
                    if (total >= MAX_ENTRIES) break;
                    if (!isItemKey(key) || seen.has(key)) continue;
                    seen.add(key);
                    items.push(key);
                    total += 1;
                }
                folders[id] = { name: cleanFolderName(folder.name) || DEFAULT_FOLDER_NAME, items };
                block.push(entry);
            } else if (isItemKey(entry) && !seen.has(entry)) {
                seen.add(entry);
                block.push(entry);
                total += 1;
            }
        }
        if (block.length) blocks.push(block);
    }
    return { v: LAYOUT_VERSION, blocks, folders };
};

/* Где лежит каждый ключ: ссылка на сам массив карточки (а не её номер — номера
   едут, когда вставляется новая карточка) и папка, если он в папке. */
const indexKeys = (layout) => {
    const where = new Map();
    for (const block of layout.blocks) {
        for (const entry of block) {
            const id = folderIdOf(entry);
            if (id) {
                for (const key of layout.folders[id]?.items || []) where.set(key, { block, folder: id });
            } else {
                where.set(entry, { block, folder: null });
            }
        }
    }
    return where;
};

const insertNear = (where, anchorKey, key, side) => {
    const loc = where.get(anchorKey);
    // Сосед в папке — новый раздел встаёт рядом с папкой, а не в неё: папку
    // собирал человек, и подкладывать туда незнакомое нельзя.
    const anchorEntry = loc.folder ? folderRef(loc.folder) : anchorKey;
    const at = loc.block.indexOf(anchorEntry);
    loc.block.splice(side === 'after' ? at + 1 : at, 0, key);
    where.set(key, { block: loc.block, folder: null });
};

/* Карточка для целиком нового блока: следом за той, где лежит последний
   известный раздел из стандартных блоков выше, иначе перед той, где первый
   известный из блоков ниже, иначе в конец. */
const newBlockIndex = (layout, where, defaultBlocks, d) => {
    for (let b = d - 1; b >= 0; b -= 1) {
        for (let i = defaultBlocks[b].length - 1; i >= 0; i -= 1) {
            const loc = where.get(defaultBlocks[b][i]);
            if (loc) return layout.blocks.indexOf(loc.block) + 1;
        }
    }
    for (let b = d + 1; b < defaultBlocks.length; b += 1) {
        for (const key of defaultBlocks[b]) {
            const loc = where.get(key);
            if (loc) return layout.blocks.indexOf(loc.block);
        }
    }
    return layout.blocks.length;
};

/** Раскладка, в которой есть каждый раздел текущего меню.
 *  defaultBlocks — стандартное меню карточками: [[ключ, …], …]. */
export const mergeLayout = (layout, defaultBlocks) => {
    const base = layout ? cloneLayout(layout) : emptyLayout();
    const where = indexKeys(base);
    defaultBlocks.forEach((dBlock, d) => {
        dBlock.forEach((key, i) => {
            if (where.has(key)) return;
            for (let j = i - 1; j >= 0; j -= 1) {
                if (where.has(dBlock[j])) {
                    insertNear(where, dBlock[j], key, 'after');
                    return;
                }
            }
            for (let j = i + 1; j < dBlock.length; j += 1) {
                if (where.has(dBlock[j])) {
                    insertNear(where, dBlock[j], key, 'before');
                    return;
                }
            }
            const block = [key];
            base.blocks.splice(newBlockIndex(base, where, defaultBlocks, d), 0, block);
            where.set(key, { block, folder: null });
        });
    });
    return base;
};

/** Строки меню сверху вниз: { type: 'item' | 'folder' | 'hr', … }.
 *  present — ключи разделов, которые меню показывает сейчас. Папка без
 *  единого видимого раздела не рисуется — кроме режима правки: туда её и
 *  наполняют. */
export const arrangeRows = (layout, present, { editing = false } = {}) => {
    const rows = [];
    layout.blocks.forEach((block, bi) => {
        const own = [];
        for (const entry of block) {
            const id = folderIdOf(entry);
            if (id) {
                const folder = layout.folders[id];
                if (!folder) continue;
                const members = folder.items.filter((key) => present.has(key));
                if (!members.length && !editing) continue;
                own.push({ type: 'folder', id, entry, block: bi, name: folder.name, count: members.length });
                for (const key of members) own.push({ type: 'item', key, entry: key, block: bi, folder: id });
            } else if (present.has(entry)) {
                own.push({ type: 'item', key: entry, entry, block: bi, folder: null });
            }
        }
        if (!own.length) return;
        if (rows.length) rows.push({ type: 'hr', block: bi });
        rows.push(...own);
    });
    return rows;
};

/** Где лежит запись: { block, folder, index } или null. */
export const locateEntry = (layout, entry) => {
    for (let bi = 0; bi < layout.blocks.length; bi += 1) {
        const block = layout.blocks[bi];
        const index = block.indexOf(entry);
        if (index !== -1) return { block: bi, folder: null, index };
        if (isFolderRef(entry)) continue;
        for (const item of block) {
            const id = folderIdOf(item);
            const at = id ? (layout.folders[id]?.items || []).indexOf(entry) : -1;
            if (at !== -1) return { block: bi, folder: id, index: at };
        }
    }
    return null;
};

/** Перенос раздела или папки. target: { block, folder, before, after } —
 *  карточка (номер в layout.blocks), папка (или null — верхний уровень) и
 *  место: ПЕРЕД записью before или СРАЗУ ЗА записью after; ни того ни другого —
 *  в конец. «Сразу за» нужно для «в конец того, что видно»: за видимыми
 *  разделами в раскладке бывают спрятанные сейчас (чужой отдел в селекторе), и
 *  перенос в конец массива уводил бы раздел за них — даже бросок на то же
 *  место менял бы раскладку. { newBlock: true } — своей новой карточкой: с
 *  `at` — перед карточкой номер at (бросили между двумя карточками), без него —
 *  в самом низу меню. Папка в папку не кладётся. */
export const moveEntry = (layout, entry, target) => {
    if (!target || (isFolderRef(entry) && target.folder)) return layout;
    const next = cloneLayout(layout);
    const from = locateEntry(next, entry);
    if (!from) return layout;
    const into = target.newBlock
        ? null
        : (target.folder ? next.folders[target.folder]?.items : next.blocks[target.block]);
    if (!into && !target.newBlock) return layout;
    (from.folder ? next.folders[from.folder].items : next.blocks[from.block]).splice(from.index, 1);
    if (target.newBlock) insertBlock(next, entry, target);
    else insertAt(into, entry, target);
    next.blocks = next.blocks.filter((block) => block.length);
    return next;
};

/* Новая карточка из одной записи. Опустевшую карточку, откуда запись унесли,
   убирают только ПОСЛЕ вставки: иначе номера карточек сдвинулись бы, и новая
   встала бы на одну ниже, чем её бросили. */
function insertBlock(layout, entry, target) {
    const at = Number.isInteger(target.at)
        ? Math.max(0, Math.min(layout.blocks.length, target.at))
        : layout.blocks.length;
    layout.blocks.splice(at, 0, [entry]);
}

function insertAt(into, entry, target) {
    const before = target.before == null || target.before === entry ? -1 : into.indexOf(target.before);
    const after = target.after == null || target.after === entry ? -1 : into.indexOf(target.after);
    if (before !== -1) into.splice(before, 0, entry);
    else if (after !== -1) into.splice(after + 1, 0, entry);
    else into.push(entry);
}

/* ── Панель разделов справа (просьба владельца 20.11.2026) ────────────────
   В правке меню остаётся как было; «Обнулить» уносит все разделы на панель, и
   меню собирают заново, перетаскивая их обратно. Что осталось на панели — при
   «Готово» встаёт в конец меню в обычном порядке. */

/** Все ключи разделов в раскладке — и на верхнем уровне, и в папках. */
export const layoutItemKeys = (layout) => {
    const keys = new Set();
    for (const block of layout?.blocks || []) {
        for (const entry of block) {
            const id = folderIdOf(entry);
            if (id) (layout.folders[id]?.items || []).forEach((key) => keys.add(key));
            else keys.add(entry);
        }
    }
    return keys;
};

/** Поставить раздел с панели на место target ({ block, folder, before, after }
 *  или { newBlock: true, at }, как у moveEntry). Раздел, который уже в
 *  раскладке, не дублируется. */
export const placeEntry = (layout, key, target = {}) => {
    if (typeof key !== 'string' || isFolderRef(key) || layoutItemKeys(layout).has(key)) return layout;
    const next = cloneLayout(layout);
    if (target.newBlock) {
        insertBlock(next, key, target);
        return next;
    }
    if (!next.blocks.length) next.blocks.push([]);
    const into = target.folder
        ? next.folders[target.folder]?.items
        : next.blocks[Math.min(Math.max(0, target.block ?? 0), next.blocks.length - 1)];
    if (!into) return layout;
    insertAt(into, key, target);
    return next;
};

/** Раздел положили на раздел — папка, как на iPhone: встаёт на место того, на
 *  который положили, внутри — он первым, принесённый вторым. key может быть
 *  ещё на панели (его нет в раскладке) или стоять в меню (тогда он уходит со
 *  своего места). Папку из раздела в папке не делают — вложенных папок нет. */
export const stackEntries = (layout, targetKey, key, { id, name }) => {
    if (!isItemKey(targetKey) || !isItemKey(key) || targetKey === key) return layout;
    if (!isFolderId(id) || layout.folders[id] || Object.keys(layout.folders).length >= MAX_FOLDERS) return layout;
    const target = locateEntry(layout, targetKey);
    if (!target || target.folder) return layout;
    const next = cloneLayout(layout);
    const from = locateEntry(next, key);
    if (from) (from.folder ? next.folders[from.folder].items : next.blocks[from.block]).splice(from.index, 1);
    // Место цели ищем заново: уход принесённого мог сдвинуть её в карточке.
    const at = locateEntry(next, targetKey);
    next.blocks[at.block].splice(at.index, 1, folderRef(id));
    next.folders[id] = { name: cleanFolderName(name) || DEFAULT_FOLDER_NAME, items: [targetKey, key] };
    next.blocks = next.blocks.filter((block) => block.length);
    return pruneEmptyFolders(next);
};

/** Папка, из которой унесли последний раздел, исчезает — как на экране
 *  «Домой». Пустой считается только папка без единого ключа: раздел, которого
 *  в меню сейчас не видно (другой отдел в селекторе), её держит. */
export const pruneEmptyFolders = (layout) => {
    const empty = Object.keys(layout?.folders || {}).filter((id) => !layout.folders[id].items.length);
    if (!empty.length) return layout;
    const next = cloneLayout(layout);
    const drop = new Set(empty.map(folderRef));
    next.blocks = next.blocks.map((block) => block.filter((entry) => !drop.has(entry))).filter((block) => block.length);
    empty.forEach((id) => delete next.folders[id]);
    return next;
};

/** Убрать раздел обратно на панель. Папку — нельзя: у неё нет места на панели. */
export const removeEntry = (layout, key) => {
    if (isFolderRef(key)) return layout;
    const loc = locateEntry(layout, key);
    if (!loc) return layout;
    const next = cloneLayout(layout);
    (loc.folder ? next.folders[loc.folder].items : next.blocks[loc.block]).splice(loc.index, 1);
    next.blocks = next.blocks.filter((block) => block.length);
    return next;
};

/** «Готово»: собранное человеком — как есть, остальные разделы меню — следом,
 *  в обычном порядке и по своим обычным карточкам. */
export const completeLayout = (layout, defaultBlocks) => {
    const next = cloneLayout(layout);
    const placed = layoutItemKeys(next);
    for (const block of defaultBlocks) {
        const rest = block.filter((key) => !placed.has(key));
        if (rest.length) next.blocks.push(rest);
    }
    return next;
};

/* Имя по образцу Finder: «Новая папка», «Новая папка 2», … */
export const nextFolderName = (layout) => {
    const names = new Set(Object.values(layout?.folders || {}).map((folder) => folder.name));
    if (!names.has(DEFAULT_FOLDER_NAME)) return DEFAULT_FOLDER_NAME;
    for (let n = 2; ; n += 1) {
        const name = `${DEFAULT_FOLDER_NAME} ${n}`;
        if (!names.has(name)) return name;
    }
};

export const newFolderId = (now = Date.now(), random = Math.random()) => (
    `f${Math.max(0, Math.floor(now)).toString(36)}${Math.floor(random * 36 ** 4).toString(36).padStart(4, '0')}`.slice(0, 16)
);

/** Новая папка: встаёт ПЕРЕД записью `before` в карточке `block` (обычно —
 *  первой строкой меню, чтобы её было видно сразу). */
export const createFolder = (layout, { id, name, block = 0, before = null }) => {
    if (!isFolderId(id) || layout.folders[id] || Object.keys(layout.folders).length >= MAX_FOLDERS) return layout;
    const next = cloneLayout(layout);
    if (!next.blocks.length) next.blocks.push([]);
    const into = next.blocks[Math.min(Math.max(0, block), next.blocks.length - 1)];
    const at = before == null ? 0 : into.indexOf(before);
    into.splice(at === -1 ? 0 : at, 0, folderRef(id));
    next.folders[id] = { name: cleanFolderName(name) || DEFAULT_FOLDER_NAME, items: [] };
    return next;
};

export const renameFolder = (layout, id, name) => {
    const clean = cleanFolderName(name);
    if (!layout.folders[id] || !clean || layout.folders[id].name === clean) return layout;
    const next = cloneLayout(layout);
    next.folders[id].name = clean;
    return next;
};

/* Папку удаляют — разделы остаются: встают на её место в том же порядке. */
export const deleteFolder = (layout, id) => {
    if (!layout.folders[id]) return layout;
    const next = cloneLayout(layout);
    const loc = locateEntry(next, folderRef(id));
    const items = next.folders[id].items;
    if (loc) next.blocks[loc.block].splice(loc.index, 1, ...items);
    delete next.folders[id];
    next.blocks = next.blocks.filter((block) => block.length);
    return next;
};

/* Сравнение без учёта порядка ключей в folders: он в JSON бывает любым. */
export const layoutSignature = (layout) => {
    if (!layout) return '';
    const folders = Object.keys(layout.folders || {}).sort().map((id) => [id, layout.folders[id].name, layout.folders[id].items]);
    return JSON.stringify([layout.blocks, folders]);
};

export const sameLayout = (a, b) => layoutSignature(a) === layoutSignature(b);
