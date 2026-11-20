/* Хранилище личной раскладки сайдбара: что сохранено, режим правки, история
 * для ⌘Z и свёрнутые папки.
 *
 * ПРАВКА (решения владельца 20.11.2026): по «Изменить меню» меню остаётся
 * как было, разделы просто перетаскивают; «Обнулить» уносит все разделы на
 * панель справа, и меню собирают заново. working — то, что стоит в сайдбаре;
 * панель — разделы меню, которых в working нет. Папка появляется, когда
 * раздел кладут на раздел, и исчезает, когда из неё унесли последний.
 * «Готово» ставит оставшееся на панели в конец меню в обычном порядке
 * (completeLayout), «Отменить» возвращает сохранённую раскладку.
 *
 * ОТДЕЛЬНОЕ ХРАНИЛИЩЕ, А НЕ СОСТОЯНИЕ App. Меню собирается внутри
 * `sidebarTree = useMemo(...)` с полусотней зависимостей; положи раскладку в
 * состояние App — и каждый шаг перетаскивания перерисовывал бы весь портал, а
 * забытая зависимость молча заморозила бы меню. Здесь же подписаны только
 * три маленьких компонента: кнопка-карандаш, полоса правки и сам список.
 *
 * Где живёт раскладка:
 *   - на сервере за учёткой (/api/sidebar_layout) — порядок один на всех
 *     устройствах человека;
 *   - копией в localStorage — с ней меню с первого кадра открывается в своём
 *     порядке, без перестройки после ответа сервера;
 *   - свёрнутые папки — только в localStorage: это вид на этом устройстве, и
 *     на каждый щелчок по папке незачем ходить на сервер.
 * «Готово» применяет раскладку сразу, а на сервер отправляет в фоне: сеть
 * подвела — копия помечена неотправленной и уходит при следующей попытке или
 * при следующем входе. */
import axios from 'axios';
import { useSyncExternalStore } from 'react';
import {
    cloneLayout,
    completeLayout,
    deleteFolder as deleteFolderIn,
    emptyLayout,
    isFolderId,
    mergeLayout,
    moveEntry,
    newFolderId,
    nextFolderName,
    normalizeLayout,
    placeEntry,
    pruneEmptyFolders,
    removeEntry,
    renameFolder as renameFolderIn,
    sameLayout,
    stackEntries,
} from './sidebarLayoutModel.js';

const HISTORY_LIMIT = 100;
const SYNC_DELAY_MS = 1500;
const RETRY_DELAYS_MS = [3000, 15000, 60000];

const cacheKey = (userId) => `otp:sidebarLayout:v1:${userId}`;
const closedKey = (userId) => `otp:sidebarFolders:v1:${userId}`;

const readJson = (key) => {
    try {
        const raw = window.localStorage.getItem(key);
        return raw ? JSON.parse(raw) : null;
    } catch (e) {
        return null;
    }
};

const writeJson = (key, value) => {
    try {
        if (value == null) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
        /* приватный режим или запрет на хранилище — раскладка всё равно на
           сервере, просто первый кадр после перезагрузки будет стандартным */
    }
};

const blank = (userId = null) => ({
    userId,
    saved: null,
    editing: false,
    working: null,
    history: [],
    future: [],
    closed: {},
    renamingId: null,
    confirmDeleteId: null,
});

const loadCached = (userId) => {
    if (userId == null) return blank(null);
    const cached = readJson(cacheKey(userId));
    const closedRaw = readJson(closedKey(userId));
    const closed = {};
    if (closedRaw && typeof closedRaw === 'object') {
        Object.entries(closedRaw).forEach(([id, value]) => {
            if (value === true && isFolderId(id)) closed[id] = true;
        });
    }
    return { ...blank(userId), saved: normalizeLayout(cached?.layout), closed };
};

let state = blank();
const listeners = new Set();
const beforeChange = new Set();
let context = { defaultBlocks: [], present: new Set() };
let transport = { apiBaseUrl: '', getHeaders: null };
let syncedUserId = null;
let syncTimer = null;
let retryTimer = null;
// Растёт с каждым «Готово»: ответ сервера, ушедший раньше, раскладку не перетирает.
let revision = 0;

const emit = () => {
    listeners.forEach((listener) => listener());
};

/* animate — замерить строки ДО перемены (для переезда на новые места);
   cascade — переезд всех разделов разом (на панель и обратно): каскадом. */
const update = (patch, { animate = false, cascade = false } = {}) => {
    if (animate) {
        beforeChange.forEach((fn) => {
            try {
                fn({ cascade });
            } catch (e) {
                /* замер для анимации не обязан удаваться */
            }
        });
    }
    state = { ...state, ...patch };
    emit();
};

const headers = () => {
    try {
        return transport.getHeaders ? transport.getHeaders() : {};
    } catch (e) {
        return {};
    }
};

const endpoint = () => `${transport.apiBaseUrl}/api/sidebar_layout`;

/* Ключи «по месту» (#…) — строки меню без подписи; их место не сохраняем:
   номер строки завтра будет другим. Пустые папки при «Готово» убираем, как
   iOS убирает пустую папку с экрана «Домой». */
const forSave = (layout) => {
    if (!layout) return null;
    let next = cloneLayout(layout);
    const keep = (entry) => !(typeof entry === 'string' && entry.startsWith('#'));
    next.blocks = next.blocks.map((block) => block.filter(keep));
    Object.values(next.folders).forEach((folder) => {
        folder.items = folder.items.filter(keep);
    });
    Object.keys(next.folders).forEach((id) => {
        if (!next.folders[id].items.length) next = deleteFolderIn(next, id);
    });
    return normalizeLayout(next);
};

/* Копия в localStorage: { layout, dirty, base }. base — отметка updated_at
   серверной строки, от которой копия пошла: по ней при следующем входе видно,
   не сохранил ли человек тем временем другую раскладку на другом устройстве. */
const cachedCopy = (userId) => readJson(cacheKey(userId));

const push = async (layout, attempt = 0) => {
    const { userId } = state;
    const rev = revision;
    if (userId == null || !transport.apiBaseUrl) return;
    const base = cachedCopy(userId)?.base ?? null;
    writeJson(cacheKey(userId), { layout, dirty: true, base });
    try {
        const { data } = layout
            ? await axios.put(endpoint(), { layout }, { headers: headers() })
            : await axios.delete(endpoint(), { headers: headers() });
        if (state.userId === userId && revision === rev) {
            writeJson(cacheKey(userId), layout ? { layout, dirty: false, base: data?.updated_at ?? null } : null);
        }
    } catch (error) {
        const status = error?.response?.status;
        if (status >= 400 && status < 500 && ![401, 408, 429].includes(status)) {
            /* Сервер раскладку не принял по форме — повтор ничего не изменит.
               На этом устройстве она остаётся до следующего входа. */
            writeJson(cacheKey(userId), layout ? { layout, dirty: false, base } : null);
            return;
        }
        if (attempt < RETRY_DELAYS_MS.length) {
            clearTimeout(retryTimer);
            retryTimer = setTimeout(() => {
                if (state.userId === userId && revision === rev) push(layout, attempt + 1);
            }, RETRY_DELAYS_MS[attempt]);
        }
    }
};

const pull = async () => {
    const { userId } = state;
    if (userId == null || !transport.apiBaseUrl) return;
    const cached = cachedCopy(userId);
    const rev = revision;
    try {
        const { data } = await axios.get(endpoint(), { headers: headers() });
        if (state.userId !== userId || revision !== rev) return;
        const serverAt = data?.updated_at ?? null;
        /* Неотправленная копия уходит, только если на сервере с тех пор ничего
           не менялось. Иначе человек уже сохранил другую раскладку на другом
           устройстве, и старая копия отсюда её бы молча затёрла. */
        if (cached?.dirty && (cached.base ?? null) === serverAt) {
            push(normalizeLayout(cached.layout));
            return;
        }
        const server = normalizeLayout(data?.layout);
        writeJson(cacheKey(userId), server ? { layout: server, dirty: false, base: serverAt } : null);
        if (!state.editing && !sameLayout(server, state.saved)) update({ saved: server }, { animate: true });
    } catch (error) {
        /* сети нет — меню живёт по своей копии */
    }
};

/* В правке working НЕ сливается с меню: всё, чего в ней нет, лежит на панели
   справа. Меню поменялось посреди правки (админ сменил отдел в селекторе) —
   новые разделы просто появляются на панели, а строки в сайдбаре по-прежнему
   рисуются ровно по working, так что номера карточек и соседи для переноса
   берутся из одного и того же. Опустевшая папка исчезает тем же шагом, что
   её опустошил, — и ⌘Z возвращает их вместе. */
const apply = (next, extra = {}, { cascade = false } = {}) => {
    const pruned = next ? pruneEmptyFolders(next) : next;
    if (!state.editing || !pruned || sameLayout(pruned, state.working)) {
        if (Object.keys(extra).length) update(extra);
        return false;
    }
    update({
        working: pruned,
        history: [...state.history, state.working].slice(-HISTORY_LIMIT),
        future: [],
        ...extra,
    }, { animate: true, cascade });
    return true;
};

export const sidebarLayoutStore = {
    getState: () => state,

    subscribe(listener) {
        listeners.add(listener);
        return () => listeners.delete(listener);
    },

    /** Замер перед каждой перестановкой — для плавного переезда строк. */
    onBeforeChange(fn) {
        beforeChange.add(fn);
        return () => beforeChange.delete(fn);
    },

    /** Текущее меню человека (его зовёт список при каждой отрисовке). */
    setContext(next) {
        context = next;
    },

    /** Синхронно, во время отрисовки: подхватить раскладку учётки из копии,
     *  ничего не рассылая подписчикам. Иначе первый кадр был бы стандартным. */
    ensureUser(userId) {
        const id = userId == null ? null : userId;
        if (state.userId === id) return;
        clearTimeout(syncTimer);
        clearTimeout(retryTimer);
        state = loadCached(id);
    },

    connect({ userId, apiBaseUrl, getHeaders }) {
        transport = { apiBaseUrl: apiBaseUrl || '', getHeaders: getHeaders || null };
        sidebarLayoutStore.ensureUser(userId);
        if (userId == null || syncedUserId === userId) return;
        syncedUserId = userId;
        clearTimeout(syncTimer);
        syncTimer = setTimeout(pull, SYNC_DELAY_MS);
    },

    /** Список ушёл со страницы (выход из учётки): незаконченная правка
     *  отменяется — иначе после нового входа меню открылось бы в режиме правки,
     *  и разделы не открывались бы щелчком. Следующий вход сверится с сервером. */
    disconnect() {
        sidebarLayoutStore.cancelEditing();
        syncedUserId = null;
        clearTimeout(syncTimer);
    },

    /** Раскладка правки — то, что уже стоит в сайдбаре (без панели). */
    workingLayout: () => (state.editing ? state.working : null),

    /** Сверка с сервером сейчас, без задержки после входа (тесты хранилища). */
    syncNow: () => pull(),

    isClosed: (id) => Boolean(state.closed[id]),

    setFolderOpen(id, open) {
        if (!isFolderId(id) || Boolean(state.closed[id]) === !open) return;
        const closed = { ...state.closed };
        if (open) delete closed[id];
        else closed[id] = true;
        if (state.userId != null) writeJson(closedKey(state.userId), Object.keys(closed).length ? closed : null);
        update({ closed });
    },

    /** «Изменить меню»: меню остаётся как было — его разделы просто
     *  перетаскивают (решение владельца: разлетаются только по «Обнулить»). */
    startEditing() {
        if (state.editing || state.userId == null) return;
        update({
            editing: true,
            working: mergeLayout(state.saved, context.defaultBlocks),
            history: [],
            future: [],
            renamingId: null,
            confirmDeleteId: null,
        });
    },

    /** «Обнулить»: все разделы и папки — на панель справа, меню собирают
     *  заново. Шаг как любой другой: ⌘Z возвращает меню. */
    reset() {
        if (!state.editing) return false;
        return apply(emptyLayout(), { renamingId: null, confirmDeleteId: null }, { cascade: true });
    },

    canReset: () => Boolean(state.editing && state.working?.blocks.length),

    /** «Готово»: собранное — как есть, оставшееся на панели — в конец меню в
     *  обычном порядке. Применяется сразу, на сервер уходит в фоне. */
    finishEditing() {
        if (!state.editing) return;
        const result = forSave(completeLayout(state.working, context.defaultBlocks));
        // Совпала со стандартным меню — личной раскладки нет вовсе (DELETE).
        const fallback = forSave(mergeLayout(null, context.defaultBlocks));
        const nextSaved = !result || sameLayout(result, fallback) ? null : result;
        const before = state.saved ? forSave(mergeLayout(state.saved, context.defaultBlocks)) : null;
        const changed = !sameLayout(nextSaved, before);
        update({
            editing: false,
            working: null,
            history: [],
            future: [],
            renamingId: null,
            confirmDeleteId: null,
            saved: changed ? nextSaved : state.saved,
        }, { animate: true, cascade: true });
        if (changed) {
            revision += 1;
            push(nextSaved);
        }
    },

    /** «Отменить» (и выход из учётки посреди правки): сохранённая раскладка
     *  возвращается как была — разделы с панели летят на свои старые места. */
    cancelEditing() {
        if (!state.editing) return;
        update({
            editing: false,
            working: null,
            history: [],
            future: [],
            renamingId: null,
            confirmDeleteId: null,
        }, { animate: true, cascade: true });
    },

    canUndo: () => state.editing && state.history.length > 0,
    canRedo: () => state.editing && state.future.length > 0,

    undo() {
        if (!sidebarLayoutStore.canUndo()) return false;
        const previous = state.history[state.history.length - 1];
        update({
            working: previous,
            history: state.history.slice(0, -1),
            future: [state.working, ...state.future].slice(0, HISTORY_LIMIT),
            renamingId: null,
            confirmDeleteId: null,
        }, { animate: true });
        return true;
    },

    redo() {
        if (!sidebarLayoutStore.canRedo()) return false;
        const [next, ...rest] = state.future;
        update({
            working: next,
            history: [...state.history, state.working].slice(-HISTORY_LIMIT),
            future: rest,
            renamingId: null,
            confirmDeleteId: null,
        }, { animate: true });
        return true;
    },

    /** Раздел с панели — в сайдбар (target как у moveEntry; без него — в конец). */
    place(key, target) {
        if (!state.editing) return false;
        const base = state.working;
        const end = { block: Math.max(0, base.blocks.length - 1), folder: null, before: null };
        return apply(placeEntry(base, key, target || end), { confirmDeleteId: null });
    },

    /** Раздел из сайдбара — обратно на панель. */
    unplace(key) {
        if (!state.editing) return false;
        return apply(removeEntry(state.working, key), { confirmDeleteId: null });
    },

    move(entry, target) {
        if (!state.editing) return false;
        return apply(moveEntry(state.working, entry, target), { confirmDeleteId: null });
    },

    /** Раздел key положили на раздел targetKey — папка из двух, как на
     *  iPhone, и сразу с полем имени («Новая папка» выделено — печатай). */
    stack(targetKey, key) {
        if (!state.editing) return null;
        const base = state.working;
        const id = newFolderId();
        const next = stackEntries(base, targetKey, key, { id, name: nextFolderName(base) });
        if (next === base || !next.folders[id]) return null;
        return apply(next, { renamingId: id, confirmDeleteId: null }) ? id : null;
    },

    startRename(id) {
        if (state.editing && state.working?.folders[id]) update({ renamingId: id, confirmDeleteId: null });
    },

    finishRename(id, name) {
        if (!state.editing) return;
        apply(renameFolderIn(state.working, id, name), { renamingId: null });
    },

    cancelRename() {
        if (state.renamingId) update({ renamingId: null });
    },

    askDelete(id) {
        if (state.editing) update({ confirmDeleteId: state.confirmDeleteId === id ? null : id, renamingId: null });
    },

    cancelDelete() {
        if (state.confirmDeleteId) update({ confirmDeleteId: null });
    },

    /* Папку удаляют — её разделы остаются в сайдбаре на её месте. */
    deleteFolder(id) {
        if (!state.editing) return;
        apply(deleteFolderIn(state.working, id), { confirmDeleteId: null, renamingId: null });
    },
};

export const useSidebarLayout = () => useSyncExternalStore(
    sidebarLayoutStore.subscribe,
    sidebarLayoutStore.getState,
    sidebarLayoutStore.getState,
);
