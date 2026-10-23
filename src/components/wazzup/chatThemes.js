import { useEffect, useState, useSyncExternalStore } from 'react';

/* Темы «Чатов ОП» — личная настройка вида окна чатов (просьба владельца
 * 09.10.2026: пять разных тем, выбор сверху; каждая так же удобна, не мешает
 * работе и следует правилам мессенджеров). Тема меняет только краски —
 * раскладка, кнопки и порядок те же. Во всех темах: свои сообщения справа и
 * цветом, чужие слева на нейтральном, время и галочки в пузыре, «прочитано»
 * отличимо от «доставлено» цветом.
 *
 * Светлые темы красят переписку — подложку, пузыри, плашки дат (chatThemes.css;
 * значения — переменными отсюда же, чтобы образец в меню и окно не разошлись).
 * «Ночь» — тёмная палитра портала на всё окно раздела: chatThemeNight.css,
 * собран scripts/build_chat_night_theme.py и грузится, только когда её выбрали.
 * «Роза» так же красит всё окно розовым: chatThemeRose.css, собран
 * scripts/build_chat_rose_theme.py. «Вишнёвый биом» — розовая палитра с
 * пиксельным пейзажем: scripts/build_chat_cherry_theme.py.
 *
 * Выбор хранится в localStorage этого браузера под id сотрудника. В тёмном
 * режиме портала окно и так тёмное — там темы не действуют и выбора нет.
 *
 * Личная тема (owners — id учёток) есть только у названных сотрудников: у
 * остальных её нет ни в меню, ни по сохранённому значению. У владельца она
 * включается сама, один раз; дальше выбор за ним, как у любой темы.
 *
 * Учёткам из STANDARD_ONLY — только стандартная тема и без своих обоев
 * (chatWallpaper.js; сервер держит тот же список — wazzup/appearance.py):
 * кнопки «Кастомизация» у них нет, сохранённый выбор не действует. */

export const DEFAULT_CHAT_THEME = 'standard';

const ALL_THEMES = Object.freeze([
    { id: 'standard', name: 'Стандартная', wall: '#f2f2f7', incoming: '#ffffff', outgoing: '#dcf8c6' },
    {
        id: 'sky', name: 'Небо', wall: 'linear-gradient(160deg, #e9f2fb 0%, #d7e6f5 100%)', swatch: '#dde9f6',
        incoming: '#ffffff', incomingLine: 'rgba(100, 116, 139, 0.16)', outgoing: '#d2e7fc',
        accent: '#2f74d0', quote: 'rgba(47, 116, 208, 0.1)', quoteInk: '#1d3b66', read: '#0a66c2', meta: '#4f6178',
        pill: 'rgba(255, 255, 255, 0.74)', pillInk: '#475569',
    },
    {
        id: 'sand', name: 'Песок', wall: 'linear-gradient(160deg, #f6f1e7 0%, #ece2d0 100%)', swatch: '#f0e7d8',
        incoming: '#fffdf9', incomingLine: 'rgba(120, 98, 60, 0.16)', outgoing: '#f6e3c1',
        accent: '#b86a12', quote: 'rgba(184, 106, 18, 0.1)', quoteInk: '#5e3708', read: '#0a6fb8', meta: '#6d5c45',
        pill: 'rgba(255, 252, 245, 0.78)', pillInk: '#6b5a43',
    },
    {
        id: 'lavender', name: 'Лаванда', wall: 'linear-gradient(160deg, #f3f0fb 0%, #e6e0f6 100%)', swatch: '#ebe6f8',
        incoming: '#ffffff', incomingLine: 'rgba(91, 78, 140, 0.14)', outgoing: '#e4dbfc',
        accent: '#7444d6', quote: 'rgba(116, 68, 214, 0.1)', quoteInk: '#3f2178', read: '#2563eb', meta: '#5d5677',
        pill: 'rgba(255, 255, 255, 0.74)', pillInk: '#5b5675',
    },
    {
        id: 'graphite', name: 'Графит', wall: '#e8eaee',
        incoming: '#ffffff', incomingLine: 'rgba(71, 85, 105, 0.14)', outgoing: '#dbe1ea',
        accent: '#475569', quote: 'rgba(71, 85, 105, 0.1)', quoteInk: '#1e293b', read: '#0369a1', meta: '#4b5563',
        pill: 'rgba(255, 255, 255, 0.78)', pillInk: '#475569',
    },
    // Подложку переписки украшает мозаика (chatThemeRose.svg в слое темы).
    {
        id: 'rose', name: 'Роза',
        wall: 'linear-gradient(160deg, #fcf3f6 0%, #f6e3ea 100%)', swatch: '#f8e8ee',
        incoming: '#ffffff', incomingLine: 'rgba(150, 64, 104, 0.14)', outgoing: '#f9dae6',
        accent: '#c0447a', quote: 'rgba(192, 68, 122, 0.1)', quoteInk: '#6a1f42', read: '#1d63c9', meta: '#7b5063',
        pill: 'rgba(255, 255, 255, 0.78)', pillInk: '#7a4a5e',
    },
    {
        id: 'cherry', name: 'Вишнёвый биом', owners: Object.freeze([292]),
        wall: '#fff0f5', swatch: '#f9dce9',
        incoming: '#fffdfd', incomingLine: 'rgba(172, 44, 101, 0.14)', outgoing: '#f6bfd6',
        accent: '#ac2c65', quote: '#f9dce9', quoteInk: '#492b3c', read: '#205980', meta: '#66334e',
        pill: '#fffdfd', pillInk: '#75495f',
    },
    {
        id: 'night', name: 'Ночь', wall: '#121519',
        incoming: '#23272e', incomingLine: 'rgba(148, 163, 184, 0.12)', outgoing: '#203b31',
        accent: '#5fbf9a', quote: 'rgba(255, 255, 255, 0.06)', quoteInk: '#c8d6cf', read: '#7dd3fc', meta: '#939cab',
        pill: 'rgba(35, 39, 46, 0.92)', pillInk: '#a8b1bf',
    },
]);

// Общие темы — у всех; к ним человек видит свои личные.
export const CHAT_THEMES = Object.freeze(ALL_THEMES.filter((theme) => !theme.owners));
const STANDARD_ONLY = Object.freeze([]);
const STANDARD_THEMES = Object.freeze(ALL_THEMES.filter((theme) => theme.id === DEFAULT_CHAT_THEME));
export function chatThemesFor(owner) {
    if (owner == null) return CHAT_THEMES;
    if (STANDARD_ONLY.includes(Number(owner))) return STANDARD_THEMES;
    return ALL_THEMES.filter((theme) => !theme.owners || theme.owners.includes(Number(owner)));
}
// Свои обои — у всех, кроме STANDARD_ONLY; без учётки — ни у кого.
export const wallpaperAllowed = (owner) => owner != null && owner !== ''
    && !STANDARD_ONLY.includes(Number(owner));

export const chatThemeById = (id) => ALL_THEMES.find((theme) => theme.id === id) || ALL_THEMES[0];

const VARIABLES = {
    wall: '--wz-wall', incoming: '--wz-incoming', incomingLine: '--wz-incoming-line', outgoing: '--wz-outgoing',
    accent: '--wz-accent', quote: '--wz-quote', quoteInk: '--wz-quote-ink', read: '--wz-read', meta: '--wz-meta',
    pill: '--wz-pill', pillInk: '--wz-pill-ink',
};

// Переменные темы для style окна; у стандартной их нет — окно как было.
export function chatThemeStyle(theme) {
    if (!theme || theme.id === DEFAULT_CHAT_THEME) return {};
    return Object.fromEntries(Object.entries(VARIABLES).map(([field, name]) => [name, theme[field]]));
}

const PREFIX = 'icore.wazzup.chatTheme.';
// Личная тема уже включалась у владельца в этом браузере (значение — её id).
const PERSONAL = PREFIX + 'personal.';
const listeners = new Set();
let cache = { owner: undefined, id: DEFAULT_CHAT_THEME };

const storage = () => {
    try { return globalThis.localStorage || null; } catch { return null; }
};
const emit = () => listeners.forEach((listener) => listener());

function readChatTheme(owner) {
    if (owner == null) return DEFAULT_CHAT_THEME;
    if (cache.owner === owner) return cache.id;
    const themes = chatThemesFor(owner);
    let id = DEFAULT_CHAT_THEME;
    try {
        const store = storage();
        const personal = themes.find((theme) => theme.owners);
        // Сначала метка: не записалась — выбор человека остаётся нетронутым.
        if (personal && store && store.getItem(PERSONAL + owner) !== personal.id) {
            store.setItem(PERSONAL + owner, personal.id);
            store.setItem(PREFIX + owner, personal.id);
        }
        const stored = store?.getItem(PREFIX + owner);
        if (themes.some((theme) => theme.id === stored)) id = stored;
    } catch { /* Хранилище недоступно — стандартная тема. */ }
    cache = { owner, id };
    return id;
}

export function setChatTheme(owner, id) {
    if (owner == null || !chatThemesFor(owner).some((theme) => theme.id === id)) return;
    cache = { owner, id };
    try {
        if (id === DEFAULT_CHAT_THEME) storage()?.removeItem(PREFIX + owner);
        else storage()?.setItem(PREFIX + owner, id);
    } catch { /* Тема держится до перезагрузки. */ }
    emit();
}

// Тему сменили в соседней вкладке — это окно перекрашивается вслед.
let storageListening = false;
const onStorage = (event) => {
    if (event.key != null && !event.key.startsWith(PREFIX)) return;
    cache = { owner: undefined, id: DEFAULT_CHAT_THEME };
    emit();
};

function subscribe(listener) {
    listeners.add(listener);
    if (!storageListening && typeof globalThis.addEventListener === 'function') {
        globalThis.addEventListener('storage', onStorage);
        storageListening = true;
    }
    return () => listeners.delete(listener);
}

/* Темы на всё окно — своим слоем стилей: он запрашивается
   один раз за сессию и только у выбравших тему. Окно переключается на тему
   ПОСЛЕ загрузки стилей — иначе между атрибутом и приходом стилей оно
   моргнуло бы наполовину перекрашенным. */
const LAYERS = {
    night: () => import('./chatThemeNight.css'),
    rose: () => import('./chatThemeRose.css'),
    cherry: () => import('./chatThemeCherry.css'),
};
let layers = {};
const layerReady = (id) => !LAYERS[id] || Boolean(layers[id]?.ready);
export function loadThemeLayer(id) {
    if (!LAYERS[id]) return Promise.resolve();
    if (!layers[id]) {
        const layer = { ready: false };
        layer.promise = LAYERS[id]().then(() => { layer.ready = true; })
            .catch((error) => { if (layers[id] === layer) delete layers[id]; throw error; });
        layers[id] = layer;
    }
    return layers[id].promise;
}
export const loadNightTheme = () => loadThemeLayer('night');

/* { selected, applied, themes, choose }: selected — выбор человека (галочка в
   меню), applied — что сейчас на экране (до загрузки слоя темы — прежний вид),
   themes — темы этого человека для меню. */
export function useChatTheme(owner) {
    const selected = useSyncExternalStore(subscribe, () => readChatTheme(owner), () => DEFAULT_CHAT_THEME);
    const [, setLoaded] = useState(0);
    useEffect(() => {
        if (layerReady(selected)) return undefined;
        let alive = true;
        loadThemeLayer(selected).then(() => { if (alive) setLoaded((count) => count + 1); }).catch(() => {});
        return () => { alive = false; };
    }, [selected]);
    const applied = layerReady(selected) ? selected : DEFAULT_CHAT_THEME;
    return { selected, applied: chatThemeById(applied), themes: chatThemesFor(owner),
        choose: (id) => setChatTheme(owner, id) };
}

/* Меню раздела (статус смены) рисуются в портал — вне окна. В теме на
   всё окно метка на body даёт им ту же палитру, пока раздел открыт
   (chatThemeNight.css, chatThemeRose.css, chatThemes.css): светлое меню над
   тёмным окном было бы вторым видом на одном экране. */
const MENU_MARKS = { night: 'data-wz-chat-night', rose: 'data-wz-chat-rose', cherry: 'data-wz-chat-cherry' };
export function useThemeMenus(id) {
    useEffect(() => {
        const mark = MENU_MARKS[id];
        if (!mark || typeof document === 'undefined') return undefined;
        document.body.setAttribute(mark, '');
        return () => document.body.removeAttribute(mark);
    }, [id]);
}

// Тёмный режим портала (src/utils/darkTheme.js) — атрибут на <html>.
const portalDark = () => typeof document !== 'undefined'
    && document.documentElement.getAttribute('data-otp-theme') === 'dark';
function subscribePortal(listener) {
    if (typeof MutationObserver !== 'function' || typeof document === 'undefined') return () => {};
    const observer = new MutationObserver(listener);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-otp-theme'] });
    return () => observer.disconnect();
}
export const usePortalDark = () => useSyncExternalStore(subscribePortal, portalDark, () => false);

// Только для тестов: модуль — синглтон вкладки.
export function resetChatThemeForTests() {
    cache = { owner: undefined, id: DEFAULT_CHAT_THEME };
    layers = {};
}
