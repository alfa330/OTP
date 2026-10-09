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
 *
 * Выбор хранится в localStorage этого браузера под id сотрудника. В тёмном
 * режиме портала окно и так тёмное — там темы не действуют и выбора нет. */

export const DEFAULT_CHAT_THEME = 'standard';

export const CHAT_THEMES = Object.freeze([
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
    {
        id: 'night', name: 'Ночь', wall: '#121519',
        incoming: '#23272e', incomingLine: 'rgba(148, 163, 184, 0.12)', outgoing: '#203b31',
        accent: '#5fbf9a', quote: 'rgba(255, 255, 255, 0.06)', quoteInk: '#c8d6cf', read: '#7dd3fc', meta: '#939cab',
        pill: 'rgba(35, 39, 46, 0.92)', pillInk: '#a8b1bf',
    },
]);

export const chatThemeById = (id) => CHAT_THEMES.find((theme) => theme.id === id) || CHAT_THEMES[0];

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
const listeners = new Set();
let cache = { owner: undefined, id: DEFAULT_CHAT_THEME };

const storage = () => {
    try { return globalThis.localStorage || null; } catch { return null; }
};
const emit = () => listeners.forEach((listener) => listener());

function readChatTheme(owner) {
    if (owner == null) return DEFAULT_CHAT_THEME;
    if (cache.owner === owner) return cache.id;
    let id = DEFAULT_CHAT_THEME;
    try {
        const stored = storage()?.getItem(PREFIX + owner);
        if (CHAT_THEMES.some((theme) => theme.id === stored)) id = stored;
    } catch { /* Хранилище недоступно — стандартная тема. */ }
    cache = { owner, id };
    return id;
}

export function setChatTheme(owner, id) {
    if (owner == null || !CHAT_THEMES.some((theme) => theme.id === id)) return;
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

/* Слой «Ночи» запрашивается один раз за сессию. Окно переключается на неё
   ПОСЛЕ загрузки стилей — иначе между атрибутом и приходом стилей оно
   моргнуло бы наполовину тёмным. */
let nightLayer = null;
let nightReady = false;
export function loadNightTheme() {
    if (!nightLayer) {
        nightLayer = import('./chatThemeNight.css').then(() => { nightReady = true; })
            .catch((error) => { nightLayer = null; throw error; });
    }
    return nightLayer;
}

/* { selected, applied, choose }: selected — выбор человека (галочка в меню),
   applied — что сейчас на экране (до загрузки «Ночи» — прежний вид). */
export function useChatTheme(owner) {
    const selected = useSyncExternalStore(subscribe, () => readChatTheme(owner), () => DEFAULT_CHAT_THEME);
    const [, setLoaded] = useState(nightReady);
    useEffect(() => {
        if (selected !== 'night' || nightReady) return undefined;
        let alive = true;
        loadNightTheme().then(() => { if (alive) setLoaded(true); }).catch(() => {});
        return () => { alive = false; };
    }, [selected]);
    const applied = selected === 'night' && !nightReady ? DEFAULT_CHAT_THEME : selected;
    return { selected, applied: chatThemeById(applied), choose: (id) => setChatTheme(owner, id) };
}

/* Меню раздела (тема, статус смены) рисуются в портал — вне окна. В «Ночи»
   метка на body даёт им ту же тёмную палитру, пока раздел открыт
   (chatThemeNight.css, chatThemes.css): светлое меню над тёмным окном было бы
   вторым видом на одном экране. */
export function useNightMenus(active) {
    useEffect(() => {
        if (!active || typeof document === 'undefined') return undefined;
        document.body.setAttribute('data-wz-chat-night', '');
        return () => document.body.removeAttribute('data-wz-chat-night');
    }, [active]);
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
    nightLayer = null;
    nightReady = false;
}
