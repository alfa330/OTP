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

import { setThemeColorMeta } from './pwa';
import { isHalloweenTime } from '../components/seasonal/halloween/halloweenWindow';

/* Цвет строки состояния установленного портала. Светлый совпадает с фоном
   портала (bg-gray-50), тёмный — с холстом тёмного слоя: у приложения на
   домашнем экране строка состояния красится этим значением, и в тёмном
   режиме светлая полоса поверх экрана выглядела бы обрезком чужой темы. */
const THEME_COLOR_LIGHT = '#f9fafb';
const THEME_COLOR_DARK = '#17191e';

export const DARK_THEME_ATTRIBUTE = 'data-otp-theme';
export const DARK_THEME_STORAGE_KEY = 'otp.theme';

/* Кому режим доступен. Список логинов в нижнем регистре. */
const DARK_THEME_LOGINS = ['sherzad'];

/* Хэллоуин-2026 (до конца 1 ноября по Астане, просьба владельца 30.10):
   праздничное оформление — этот же тёмный слой с оранжевыми акцентами — у
   всех, и на экране входа тоже. Тычок в аватар на эти дни есть у каждого:
   кому темно, вернёт светлую, и выбор запомнится словом 'light'. После срока
   право снова решает только список логинов, а 'light' читается как «светлая». */
export const canUseDarkTheme = (user) => {
    if (isHalloweenTime()) return true;
    const login = String(user?.login || '').trim().toLowerCase();
    return !!login && DARK_THEME_LOGINS.includes(login);
};

/* Слой запрашивается один раз за сессию: повторное включение уже ничего не
   грузит, промис переиспользуется. В праздник вместе с ним — оранжевые
   акценты; их селекторы весят так, что порядок загрузки двух файлов неважен. */
let stylesheetPromise = null;
const loadStylesheet = () => {
    if (!stylesheetPromise) {
        stylesheetPromise = Promise.all([
            import('../theme-dark.css'),
            isHalloweenTime() ? import('../components/seasonal/halloween/halloween-theme.css') : null,
        ]);
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
        setThemeColorMeta(THEME_COLOR_LIGHT);
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
    setThemeColorMeta(THEME_COLOR_DARK);
};

export const readStoredDarkTheme = () => {
    try {
        const stored = localStorage.getItem(DARK_THEME_STORAGE_KEY);
        return isHalloweenTime() ? stored !== 'light' : stored === 'dark';
    } catch (error) {
        return isHalloweenTime();
    }
};

export const storeDarkTheme = (enabled) => {
    try {
        if (enabled) {
            localStorage.setItem(DARK_THEME_STORAGE_KEY, 'dark');
        } else if (isHalloweenTime()) {
            localStorage.setItem(DARK_THEME_STORAGE_KEY, 'light');
        } else {
            localStorage.removeItem(DARK_THEME_STORAGE_KEY);
        }
    } catch (error) {
        /* Приватный режим/запрет хранилища: тема доживёт до перезагрузки. */
    }
};
