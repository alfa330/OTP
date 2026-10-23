import { useEffect, useSyncExternalStore } from 'react';
import axios from 'axios';

/* Обои «Чатов ОП» — своя картинка на фоне переписки (wazzup/appearance.py).
 *
 * Тема окна живёт в браузере (chatThemes.js), а обои — на сервере: картинку
 * загружают один раз и видят на любом компьютере, а админ видит, у кого какие.
 *
 * Хранилище одно на вкладку (как workspaceStore.js): окно раздела и окно
 * «Кастомизация» читают одно и то же, и сохранённое в окне сразу видно в разделе.
 *
 * Картинку отдаёт API под входом, поэтому её качаем запросом с заголовками и
 * показываем через object URL — обычный url() в стилях заголовков не несёт.
 * Адрес у каждой картинки свой и неизменный: повторный вход берёт её из кэша
 * браузера. Последний известный вид помним в localStorage — обои начинают
 * грузиться сразу, не дожидаясь ответа сервера о том, какие они. */

export const DEFAULT_WALLPAPER_SETTINGS = Object.freeze({ brightness: 0, blur: 0, saturation: 100, x: 50, y: 50 });
// Границы — те же, что проверяет сервер (SETTINGS в wazzup/appearance.py).
export const WALLPAPER_LIMITS = Object.freeze({
    brightness: Object.freeze({ min: -60, max: 60 }),
    blur: Object.freeze({ min: 0, max: 20 }),
    saturation: Object.freeze({ min: 0, max: 200 }),
    x: Object.freeze({ min: 0, max: 100 }),
    y: Object.freeze({ min: 0, max: 100 }),
});
// Самая длинная сторона картинки, которую браузер отправляет (сервер ужмёт так же).
export const WALLPAPER_MAX_SIDE = 2560;
const MAX_SOURCE_BYTES = 40 * 1024 * 1024;

const EMPTY = Object.freeze({
    owner: null,
    ready: false,       // сервер ответил хотя бы раз
    wallpaper: null,    // { id, width, height, updatedAt }
    settings: DEFAULT_WALLPAPER_SETTINGS,
    mayUpload: false,
    url: '',            // загруженная картинка текущих обоев
});

const CACHE = 'icore.wazzup.chatWallpaper.';
let state = EMPTY;
let api = null;         // { apiBaseUrl, owner, headers }
let epoch = 0;          // смена человека: ответы прежнего не ложатся в состояние нового
let loadInFlight = null;
const images = new Map();
const listeners = new Set();

const emit = (patch) => {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
};
const subscribe = (listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
};
const storage = () => {
    try { return globalThis.localStorage || null; } catch { return null; }
};

export const cleanWallpaperSettings = (value) => {
    const result = { ...DEFAULT_WALLPAPER_SETTINGS };
    if (value && typeof value === 'object') {
        for (const [key, { min, max }] of Object.entries(WALLPAPER_LIMITS)) {
            const item = Number(value[key]);
            if (value[key] !== null && value[key] !== '' && typeof value[key] !== 'boolean'
                && Number.isFinite(item) && item >= min && item <= max) result[key] = item;
        }
    }
    return result;
};

function remembered(owner) {
    try {
        const value = JSON.parse(storage()?.getItem(CACHE + owner) || 'null');
        if (!value || typeof value.id !== 'string') return null;
        return { wallpaper: { id: value.id, width: Number(value.width) || 0, height: Number(value.height) || 0 },
            settings: cleanWallpaperSettings(value.settings) };
    } catch {
        return null;
    }
}

function remember(owner, wallpaper, settings) {
    try {
        if (!wallpaper) storage()?.removeItem(CACHE + owner);
        else storage()?.setItem(CACHE + owner, JSON.stringify({
            id: wallpaper.id, width: wallpaper.width, height: wallpaper.height, settings }));
    } catch { /* Без памяти браузера обои просто появятся после ответа сервера. */ }
}

const endpoint = (path = '') => `${api.apiBaseUrl}/api/wazzup/appearance${path}`;
const errorText = (error, fallback) => error?.response?.data?.error || fallback;

/* Картинка обоев по id: object URL, один на вкладку. thumb — миниатюра (список у админа). */
export function wallpaperImage(id, { thumb = false } = {}) {
    const key = `${id}:${thumb ? 'thumb' : 'full'}`;
    if (!api) return Promise.reject(new Error('Обои не настроены'));
    let entry = images.get(key);
    if (!entry) {
        const owner = epoch;
        entry = { url: '' };
        entry.promise = axios.get(endpoint(`/wallpaper/${encodeURIComponent(id)}`), {
            params: thumb ? { size: 'thumb' } : undefined,
            headers: api.headers(), responseType: 'blob', timeout: 30000,
        }).then(({ data }) => {
            if (owner !== epoch) throw new Error('Учётка сменилась');
            entry.url = URL.createObjectURL(data);
            return entry.url;
        }).catch((error) => {
            if (images.get(key) === entry) images.delete(key);
            throw error;
        });
        images.set(key, entry);
    }
    return entry.promise;
}

function showImage(wallpaper) {
    const id = wallpaper?.id;
    if (!id) return;
    const owner = epoch;
    wallpaperImage(id).then((url) => {
        if (owner === epoch && state.wallpaper?.id === id && state.url !== url) emit({ url });
    }).catch(() => { /* Картинки нет — окно остаётся с фоном темы. */ });
}

function apply(data) {
    const wallpaper = data?.wallpaper && typeof data.wallpaper.id === 'string' ? data.wallpaper : null;
    const settings = cleanWallpaperSettings(data?.settings);
    const sameImage = wallpaper && state.wallpaper?.id === wallpaper.id;
    emit({ ready: true, wallpaper, settings, mayUpload: Boolean(data?.mayUpload), url: sameImage ? state.url : '' });
    remember(api.owner, wallpaper, settings);
    showImage(wallpaper);
}

export function configureChatWallpaper({ apiBaseUrl, owner, headers }) {
    if (api && (api.owner !== owner || api.apiBaseUrl !== apiBaseUrl)) resetChatWallpaper();
    const fresh = !api;
    api = { apiBaseUrl, owner, headers: () => (typeof headers === 'function' ? headers() : {}) || {} };
    if (fresh) {
        const known = remembered(owner);
        emit({ ...EMPTY, owner, ...(known || {}) });
        if (known) showImage(known.wallpaper);
    }
}

export function resetChatWallpaper() {
    epoch += 1;
    api = null;
    loadInFlight = null;
    images.forEach((entry) => { if (entry.url) URL.revokeObjectURL(entry.url); });
    images.clear();
    state = EMPTY;
    listeners.forEach((listener) => listener());
}

export async function loadChatWallpaper() {
    if (!api) return;
    if (loadInFlight) return loadInFlight.promise;
    const owner = epoch;
    const flight = {};
    flight.promise = axios.get(endpoint(), { headers: api.headers(), timeout: 20000 }).then(({ data }) => {
        if (owner === epoch) apply(data);
    }).catch(() => {
        // Не узнали — показываем то, что помнили; следующий вход спросит снова.
        if (owner === epoch && !state.ready) emit({ ready: true });
    }).finally(() => {
        if (loadInFlight === flight) loadInFlight = null;
    });
    loadInFlight = flight;
    return flight.promise;
}

/* Сохранение из окна «Кастомизация». image — новая картинка ({ blob, url }),
   remove — убрать обои; иначе — только вид тех обоев, что стоят. Ошибка —
   исключение с текстом для человека. */
export async function saveChatWallpaper({ image = null, remove = false, settings }) {
    if (!api) throw new Error('Обои недоступны');
    const owner = epoch;
    const clean = cleanWallpaperSettings(settings);
    try {
        let data;
        if (image) {
            const form = new FormData();
            form.append('file', image.blob, image.blob.type === 'image/jpeg' ? 'wallpaper.jpg' : 'wallpaper.webp');
            form.append('settings', JSON.stringify(clean));
            ({ data } = await axios.post(endpoint('/wallpaper'), form, { headers: api.headers(), timeout: 90000 }));
            /* Сервер пересжал ту же картинку — показываем уже загруженную у себя,
               а не качаем её обратно. */
            if (owner === epoch && data?.wallpaper?.id && image.url) {
                const key = `${data.wallpaper.id}:full`;
                if (!images.has(key)) images.set(key, { url: image.url, promise: Promise.resolve(image.url) });
            }
        } else if (remove) {
            ({ data } = await axios.delete(endpoint('/wallpaper'), { headers: api.headers(), timeout: 20000 }));
        } else {
            ({ data } = await axios.put(endpoint(), { wallpaperId: state.wallpaper?.id, settings: clean },
                { headers: api.headers(), timeout: 20000 }));
        }
        if (owner === epoch) apply(data);
        return data;
    } catch (error) {
        // 409 — обои сменили в другой вкладке: показываем те, что стоят теперь.
        if (owner === epoch && error?.response?.status === 409 && error.response.data) apply(error.response.data);
        throw new Error(errorText(error, 'Не удалось сохранить обои. Попробуйте ещё раз.'));
    }
}

/* Список «у кого какие обои» — только админам (сервер проверяет сам). */
export async function loadStaffWallpapers() {
    if (!api) return [];
    const { data } = await axios.get(endpoint('/wallpapers'), { headers: api.headers(), timeout: 20000 });
    return Array.isArray(data?.items) ? data.items.map((item) => ({
        ...item, settings: cleanWallpaperSettings(item.settings) })) : [];
}

/* Картинка с диска -> { blob, url, width, height }: повёрнута по EXIF, длинная
   сторона не больше WALLPAPER_MAX_SIDE, WebP (JPEG, где браузер WebP не пишет).
   Холст заодно отрезает метаданные снимка. */
export async function prepareWallpaperFile(file) {
    if (!file) throw new Error('Выберите картинку');
    if (file.type && !file.type.startsWith('image/')) throw new Error('Это не картинка. Подойдут JPG, PNG или WebP.');
    if (file.size > MAX_SOURCE_BYTES) throw new Error('Картинка больше 40 МБ');
    let source;
    let release = () => {};
    try {
        source = await createImageBitmap(file, { imageOrientation: 'from-image' });
        release = () => source.close?.();
    } catch {
        const url = URL.createObjectURL(file);
        try {
            const image = new Image();
            image.src = url;
            await image.decode();
            source = image;
        } catch {
            URL.revokeObjectURL(url);
            throw new Error('Не удалось открыть картинку. Подойдут JPG, PNG или WebP.');
        }
        release = () => URL.revokeObjectURL(url);
    }
    try {
        const sourceWidth = source.naturalWidth || source.width;
        const sourceHeight = source.naturalHeight || source.height;
        if (!sourceWidth || !sourceHeight) throw new Error('Не удалось открыть картинку. Подойдут JPG, PNG или WebP.');
        const scale = Math.min(1, WALLPAPER_MAX_SIDE / Math.max(sourceWidth, sourceHeight));
        const width = Math.max(1, Math.round(sourceWidth * scale));
        const height = Math.max(1, Math.round(sourceHeight * scale));
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext('2d');
        context.imageSmoothingQuality = 'high';
        context.drawImage(source, 0, 0, width, height);
        const encode = (target, type) => new Promise((resolve) => target.toBlob(resolve, type, 0.9));
        let blob = await encode(canvas, 'image/webp');
        if (!blob || blob.type !== 'image/webp') {
            // JPEG прозрачности не знает: прозрачное ляжет на белое, а не на чёрное.
            const flat = document.createElement('canvas');
            flat.width = width;
            flat.height = height;
            const flatContext = flat.getContext('2d');
            flatContext.fillStyle = '#ffffff';
            flatContext.fillRect(0, 0, width, height);
            flatContext.drawImage(canvas, 0, 0);
            blob = await encode(flat, 'image/jpeg');
        }
        if (!blob) throw new Error('Не удалось подготовить картинку');
        return { blob, url: URL.createObjectURL(blob), width, height };
    } finally {
        release();
    }
}

/* Обои человека для окна раздела. owner = null — обоев нет (тёмный режим
   портала, учётка без обоев): ни запросов, ни картинки. */
export function useChatWallpaper(owner, { apiBaseUrl, headers }) {
    useEffect(() => {
        if (owner == null) return;
        configureChatWallpaper({ apiBaseUrl, owner, headers });
    });
    useEffect(() => {
        if (owner == null) return;
        configureChatWallpaper({ apiBaseUrl, owner, headers });
        loadChatWallpaper();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [owner, apiBaseUrl]);
    const snapshot = useSyncExternalStore(subscribe, () => state, () => EMPTY);
    return owner != null && snapshot.owner === owner ? snapshot : EMPTY;
}

// Только для тестов: модуль — синглтон вкладки.
export function resetChatWallpaperForTests() {
    resetChatWallpaper();
}
