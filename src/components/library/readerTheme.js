/*
 * Тема страниц ридера (задача #405): день — тёплая бумага, ночь — чёрные
 * страницы и белый текст. Переключает сам читатель кнопкой в шапке книги.
 *
 * Выбор живёт на устройстве (localStorage): тема — привычка конкретного
 * экрана и времени суток, а не настройка аккаунта, и запроса к серверу ради
 * неё не нужно. Пока читатель ничего не выбирал, книга следует теме портала:
 * в тёмном режиме портала (src/utils/darkTheme.js) она открывается ночной.
 *
 * Хранилище бывает недоступно (приватный режим Safari, запрет политикой) —
 * тогда тема просто не запоминается, а ридер работает как обычно.
 */

export const READER_THEME_KEY = 'otp.library.theme';

const NIGHT = 'night';
const DAY = 'day';

const browserStorage = () => {
    try {
        return typeof window !== 'undefined' ? window.localStorage : null;
    } catch {
        return null;
    }
};

const documentRoot = () => (typeof document !== 'undefined' ? document.documentElement : null);

/* true — ночь, false — день, null — читатель ещё не выбирал. */
export const readReaderNight = (storage = browserStorage()) => {
    try {
        const value = storage?.getItem(READER_THEME_KEY);
        if (value === NIGHT) return true;
        if (value === DAY) return false;
    } catch { /* хранилище недоступно */ }
    return null;
};

export const writeReaderNight = (night, storage = browserStorage()) => {
    try {
        storage?.setItem(READER_THEME_KEY, night ? NIGHT : DAY);
    } catch { /* не запомним — не беда */ }
};

/* Тёмный режим портала — атрибут на <html> (src/utils/darkTheme.js). */
export const portalIsDark = (root = documentRoot()) => root?.getAttribute?.('data-otp-theme') === 'dark';

export const initialReaderNight = (storage = browserStorage(), root = documentRoot()) => {
    const chosen = readReaderNight(storage);
    return chosen === null ? portalIsDark(root) : chosen;
};
