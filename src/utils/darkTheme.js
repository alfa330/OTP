/*
 * Тёмный режим портала — персональная настройка.
 *
 * Портал светлый и остаётся светлым: разметку под тему никто не переписывал,
 * классов `dark:*` в разделах нет намеренно (Tailwind собран без darkMode, они
 * сработали бы от темы СИСТЕМЫ, а не от выбора человека). Тёмный режим —
 * отдельный слой поверх собранных утилит: `src/theme-dark.css`, собирается
 * скриптом `scripts/build_dark_theme.py` и включается атрибутом
 * `data-otp-theme="dark"` на <html>.
 *
 * Три решения, которые стоит объяснить.
 *
 * 1. Режим выдан поимённо, а не всем. Это не «настройка портала», о которой
 *    договаривались с отделами, а личный режим одного аккаунта: слой в 900
 *    правил перекрывает цвета всего продукта, и выпускать его на операторов до
 *    того, как каждый раздел просмотрен глазами, нельзя.
 *
 * 2. Файл слоя грузится по требованию (динамический import). Он весит около
 *    76 КБ и не нужен никому, кроме одного аккаунта в тёмном режиме, —
 *    в общий бандл ему нельзя.
 *
 * 3. Атрибут ставится ПОСЛЕ загрузки слоя. Иначе между установкой атрибута и
 *    приходом стилей экран моргнул бы светлым.
 */

export const DARK_THEME_ATTRIBUTE = 'data-otp-theme';
export const DARK_THEME_STORAGE_KEY = 'otp.theme';

/* Кому режим доступен. Список логинов в нижнем регистре. */
const DARK_THEME_LOGINS = ['sherzad'];

export const canUseDarkTheme = (user) => {
    const login = String(user?.login || '').trim().toLowerCase();
    return !!login && DARK_THEME_LOGINS.includes(login);
};

/* Слой запрашивается один раз за сессию: повторное включение уже ничего не
   грузит, промис переиспользуется. */
let stylesheetPromise = null;
const loadStylesheet = () => {
    if (!stylesheetPromise) {
        stylesheetPromise = import('../theme-dark.css');
    }
    return stylesheetPromise;
};

/* isStale — проверка «пока грузились стили, решение уже отменили»: выход из
   аккаунта или повторный тычок по аватару не должны включить тему задним
   числом. */
export const applyDarkTheme = async (enabled, isStale) => {
    const root = typeof document !== 'undefined' ? document.documentElement : null;
    if (!root) return;
    if (!enabled) {
        root.removeAttribute(DARK_THEME_ATTRIBUTE);
        return;
    }
    try {
        await loadStylesheet();
    } catch (error) {
        /* Чанк со стилями не доехал (обновилась сборка, сеть отвалилась) —
           остаёмся в светлой теме: она рабочая, а тема без стилей — нет. */
        stylesheetPromise = null;
        return;
    }
    if (typeof isStale === 'function' && isStale()) return;
    root.setAttribute(DARK_THEME_ATTRIBUTE, 'dark');
};

export const readStoredDarkTheme = () => {
    try {
        return localStorage.getItem(DARK_THEME_STORAGE_KEY) === 'dark';
    } catch (error) {
        return false;
    }
};

export const storeDarkTheme = (enabled) => {
    try {
        if (enabled) {
            localStorage.setItem(DARK_THEME_STORAGE_KEY, 'dark');
        } else {
            localStorage.removeItem(DARK_THEME_STORAGE_KEY);
        }
    } catch (error) {
        /* Приватный режим/запрет хранилища: тема доживёт до перезагрузки. */
    }
};
