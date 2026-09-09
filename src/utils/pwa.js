/*
 * Портал как приложение на телефоне.
 *
 * Люди открывают портал с телефона десятки раз за смену, и каждый раз это
 * вкладка в браузере: адресная строка, панель вкладок, поиск нужной вкладки
 * среди тридцати чужих. Установка на домашний экран убирает всё это — значок
 * запускает портал во весь экран, как обычное приложение. Ставится он
 * средствами самого браузера (PWA): манифест `public/manifest.webmanifest`,
 * сервис-воркер `public/sw.js` и вот этот модуль — та часть, что решает, КОГДА
 * предложить установку и что показать человеку.
 *
 * Почему установка предлагается по-разному на Android и на iPhone.
 *
 * Android/Chrome отдаёт событие `beforeinstallprompt`: его можно придержать и
 * вызвать системное окно установки нашей кнопкой. iOS такого события не знает
 * вовсе — установка там живёт руками в меню «Поделиться», и единственное, что
 * мы можем, это показать понятную подсказку с теми же значками, что человек
 * увидит в Safari. Поэтому у экрана установки две ветки, и обе настоящие.
 *
 * Почему состояние держится в МОДУЛЕ, а не в состоянии App. Событие
 * `beforeinstallprompt` приходит когда захочет браузер — обычно раньше, чем
 * человек успел войти в портал, то есть до того, как отрисовалось дерево с
 * подпиской. Модуль ловит его с первой секунды жизни страницы и раздаёт
 * подписчикам; компоненты подписываются, когда появятся. Тот же приём, что у
 * окна «Новость дня» с тычком колокола: значение, положенное в состояние App,
 * пришлось бы протаскивать через мемоизированное дерево сайдбара, где список
 * зависимостей на сорок значений.
 */

/* ЗАКРЫТИЕ ПАНЕЛИ КРЕСТИКОМ БОЛЬШЕ НЕ ПРЯЧЕТ ЕЁ НАДОЛГО.
 *
 * История правила. Сначала крестик прятал предложение на две недели, потом на
 * три дня — и оба раза владелец возвращался с одним и тем же: «после обновления
 * страницы уведомление не выходит». Причина каждый раз была не в коде: он один
 * раз закрывал панель при проверке, и она честно замолкала на весь срок.
 * Воспроизведено на живом портале 09.09.2026: чистый профиль — панель есть,
 * крестик — в localStorage ложится отсрочка на три дня, обновление — пусто.
 *
 * Теперь крестик закрывает панель ТОЛЬКО НА ТЕКУЩУЮ ЗАГРУЗКУ страницы
 * (отметка живёт в памяти вкладки, см. InstallAppPrompt). Обновил страницу —
 * предложение снова здесь, как и просили.
 *
 * Чтобы это не превратилось в навязчивость, считаем закрытия: три подряд — и
 * панель умолкает на три дня. Три раза человек уже сказал «нет» достаточно
 * внятно, а один случайный тычок больше не стоит ему потерянной недели.
 *
 * Долгая отсрочка ставится ещё в одном случае — когда портал установлен
 * (событие appinstalled): предлагать больше нечего. */
export const INSTALL_SNOOZE_KEY = 'otp.install_offer_v3';
export const INSTALL_SNOOZE_MS = 3 * 24 * 60 * 60 * 1000;
export const INSTALL_DISMISS_LIMIT = 3;

/* Ключи прошлых версий: в них лежат отсрочки по прежним правилам, и без
   подчистки те, кто закрыл панель вчера, не увидели бы её ещё три дня. */
const LEGACY_SNOOZE_KEYS = ['otp.install_offer_v1', 'otp.install_offer_v2'];

/* Встроенные браузеры мессенджеров и соцсетей. В них «Добавить на экран
   Домой» нет ни в каком виде, и подсказка про «Поделиться» отправила бы
   человека искать несуществующий пункт. Список тот же, что у проверки
   cookie-ограниченного контекста в App.jsx. */
const EMBEDDED_WEBVIEW_RE = /\bwv\b|; wv\)|fbav|fban|instagram|line\/|tgweb|telegrambot|micromessenger/i;

const IOS_UA_RE = /iphone|ipad|ipod/i;
const ANDROID_UA_RE = /android/i;

/** Запущен ли портал уже как приложение (с иконки), а не во вкладке. */
export const isStandaloneDisplay = (win = typeof window === 'undefined' ? null : window) => {
    if (!win) return false;
    /* `navigator.standalone` — единственный признак на iOS: display-mode там
       появился поздно, и на живых телефонах он ещё встречается пустым. */
    if (win.navigator && win.navigator.standalone === true) return true;
    if (typeof win.matchMedia !== 'function') return false;
    return ['standalone', 'fullscreen', 'minimal-ui'].some((mode) => {
        try {
            return win.matchMedia('(display-mode: ' + mode + ')').matches;
        } catch (error) {
            return false;
        }
    });
};

/**
 * Платформа глазами установки: 'ios' | 'android' | 'desktop' | 'webview'.
 *
 * iPad с iPadOS 13+ представляется маком: отличаем по касаниям — у настоящего
 * мака `maxTouchPoints` равен нулю. Без этой поправки владельцы планшетов
 * никогда не увидели бы предложения установить портал.
 */
export const detectInstallPlatform = (nav = typeof navigator === 'undefined' ? null : navigator) => {
    if (!nav) return 'desktop';
    const ua = String(nav.userAgent || '');
    if (EMBEDDED_WEBVIEW_RE.test(ua)) return 'webview';
    if (IOS_UA_RE.test(ua)) return 'ios';
    if (String(nav.platform || '') === 'MacIntel' && Number(nav.maxTouchPoints || 0) > 1) return 'ios';
    if (ANDROID_UA_RE.test(ua)) return 'android';
    return 'desktop';
};

const safeLocalStorage = () => {
    try {
        return typeof window === 'undefined' ? null : window.localStorage;
    } catch (error) {
        /* Приватный режим и «блокировать данные сайтов» бросают на самом
           обращении к localStorage, а не на чтении. */
        return null;
    }
};

/* В хранилище лежит один объект: сколько раз закрывали и до какого момента
   молчим. Любое непонятное значение читается как «ничего не было» — испорченная
   запись не должна отменять предложение. */
const readInstallOfferState = (storage = safeLocalStorage()) => {
    try {
        const parsed = JSON.parse(storage && storage.getItem(INSTALL_SNOOZE_KEY));
        if (!parsed || typeof parsed !== 'object') return { dismissals: 0, until: 0 };
        const dismissals = Number(parsed.dismissals);
        const until = Number(parsed.until);
        return {
            dismissals: Number.isFinite(dismissals) && dismissals > 0 ? dismissals : 0,
            until: Number.isFinite(until) && until > 0 ? until : 0,
        };
    } catch (error) {
        return { dismissals: 0, until: 0 };
    }
};

const writeInstallOfferState = (state, storage = safeLocalStorage()) => {
    try {
        if (storage) storage.setItem(INSTALL_SNOOZE_KEY, JSON.stringify(state));
    } catch (error) {
        /* Не записалось — предложение придёт снова в следующий раз. Это
           неприятно, но это не повод падать. */
    }
};

/** До какого момента предложение отложено (мс эпохи; 0 — не откладывали). */
export const readInstallSnoozeUntil = (storage = safeLocalStorage()) => readInstallOfferState(storage).until;

/** Сколько раз панель закрывали крестиком. */
export const readInstallDismissals = (storage = safeLocalStorage()) => readInstallOfferState(storage).dismissals;

/**
 * Панель закрыли крестиком. На текущей загрузке страницы её больше не
 * показывают (это решает сам компонент), а здесь считаем отказы: три подряд —
 * и умолкаем на три дня.
 */
export const noteInstallOfferDismissed = (now = Date.now(), storage = safeLocalStorage()) => {
    const state = readInstallOfferState(storage);
    const dismissals = state.dismissals + 1;
    if (dismissals >= INSTALL_DISMISS_LIMIT) {
        writeInstallOfferState({ dismissals: 0, until: now + INSTALL_SNOOZE_MS }, storage);
        return;
    }
    writeInstallOfferState({ dismissals, until: state.until }, storage);
};

/** Долгая отсрочка. Ставится, когда предлагать больше нечего — портал уже
    установлен. */
export const snoozeInstallOffer = (now = Date.now(), storage = safeLocalStorage()) => {
    writeInstallOfferState({ dismissals: 0, until: now + INSTALL_SNOOZE_MS }, storage);
};

/**
 * Показывать ли экран установки САМ, без просьбы человека.
 *
 * Правило одно: предлагаем только там, где предложение можно довести до конца.
 * На Android — когда браузер отдал `beforeinstallprompt` (значит установка
 * доступна и окно откроется нашей кнопкой). На iOS — всегда, кроме встроенных
 * браузеров: события там нет, но пункт «На экран „Домой“» есть. На компьютере
 * баннер не показываем вовсе: там установка ставится значком в адресной строке,
 * а всплывающая панель посреди работы — это шум.
 */
export const shouldOfferInstall = ({
    standalone = false,
    platform = 'desktop',
    canPrompt = false,
    snoozedUntil = 0,
    now = Date.now(),
} = {}) => {
    if (standalone) return false;
    if (snoozedUntil > now) return false;
    if (platform === 'ios') return true;
    if (platform === 'android') return canPrompt;
    return false;
};

/* ==== Состояние модуля и подписка ==================================== */

const state = {
    standalone: isStandaloneDisplay(),
    platform: detectInstallPlatform(),
    canPrompt: false,
    /* Счётчики просьб открыть установку руками — из пункта меню «Установить
       приложение». Счётчики, а не флаги: повторный тычок после закрытия обязан
       открыть экран снова. Их два, потому что просьбы разные: показать панель
       с предложением и сразу открыть подробную инструкцию. */
    manualRequests: 0,
    manualGuideRequests: 0,
};

let deferredPrompt = null;
const listeners = new Set();

const publish = () => {
    const snapshot = getInstallState();
    listeners.forEach((listener) => {
        try {
            listener(snapshot);
        } catch (error) {
            console.warn('Install state listener failed:', error);
        }
    });
};

export const getInstallState = () => ({ ...state });

export const subscribeToInstallState = (listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
};

/** Открыть панель с предложением по просьбе человека — мимо «Позже». */
export const requestInstallSheet = () => {
    state.manualRequests += 1;
    publish();
};

/** Открыть подробную инструкцию, минуя панель. */
export const requestInstallGuide = () => {
    state.manualGuideRequests += 1;
    publish();
};

/**
 * Системное окно установки (Android/Chrome). Возвращает 'accepted',
 * 'dismissed' или 'unavailable'.
 *
 * Придержанное событие ОДНОРАЗОВОЕ: после `prompt()` его нельзя вызвать
 * второй раз, браузер пришлёт новое сам. Поэтому ссылку снимаем сразу, иначе
 * вторая кнопка «Установить» упала бы с InvalidStateError.
 */
export const promptInstall = async () => {
    const event = deferredPrompt;
    if (!event) return 'unavailable';
    deferredPrompt = null;
    state.canPrompt = false;
    publish();
    try {
        event.prompt();
        const choice = await event.userChoice;
        return choice && choice.outcome === 'accepted' ? 'accepted' : 'dismissed';
    } catch (error) {
        console.warn('Install prompt failed:', error);
        return 'unavailable';
    }
};

/**
 * Цвет строки состояния в установленном приложении. Меняется на ходу —
 * тёмный слой портала подключается после входа, а мета в index.html одна.
 */
export const setThemeColorMeta = (color) => {
    if (typeof document === 'undefined') return;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', color);
};

/* ==== Сервис-воркер ================================================== */

/**
 * Регистрация воркера. Путь и область берутся из base сборки: на GitHub Pages
 * портал живёт в /OTP/, и воркер, зарегистрированный от корня, не получил бы
 * права на свои же страницы.
 */
export const registerServiceWorker = (baseUrl = '/') => {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return Promise.resolve(null);
    const scope = baseUrl.endsWith('/') ? baseUrl : baseUrl + '/';
    return navigator.serviceWorker.register(scope + 'sw.js', { scope }).catch((error) => {
        /* Регистрация падает штатно: http без TLS, приватное окно, запрет
           политикой. Портал обязан работать и без воркера — это добавка,
           а не часть приложения. */
        console.warn('Service worker registration failed:', error);
        return null;
    });
};

/** Снять воркер — на локальной разработке, где кэш только мешает. */
const unregisterServiceWorkers = async () => {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return;
    try {
        const registrations = await navigator.serviceWorker.getRegistrations();
        await Promise.all(registrations.map((registration) => registration.unregister()));
    } catch (error) {
        console.warn('Service worker cleanup failed:', error);
    }
};

/**
 * Запуск всего описанного. Зовётся один раз из main.jsx.
 *
 * `withServiceWorker` выключен на разработке намеренно: воркер отдавал бы из
 * кэша файлы, которые Vite только что пересобрал, и правка не появлялась бы на
 * экране. Заодно снимаем воркер, если он остался от прогона собранной версии
 * на том же адресе.
 */
export const startPwaRuntime = ({ baseUrl = '/', withServiceWorker = true } = {}) => {
    if (typeof window === 'undefined') return;

    try {
        const storage = safeLocalStorage();
        if (storage) LEGACY_SNOOZE_KEYS.forEach((key) => storage.removeItem(key));
    } catch (error) {
        /* Хранилище запрещено — чистить нечего. */
    }

    window.addEventListener('beforeinstallprompt', (event) => {
        /* Без preventDefault Chrome показывает свою мини-полосу внизу экрана —
           поверх неё наш экран установки выглядел бы вторым предложением
           подряд. */
        event.preventDefault();
        deferredPrompt = event;
        state.canPrompt = true;
        publish();
    });

    window.addEventListener('appinstalled', () => {
        deferredPrompt = null;
        state.canPrompt = false;
        /* Вкладка, из которой ставили, остаётся вкладкой: standalone тут не
           появится. Но предлагать установку ей больше нечего. */
        snoozeInstallOffer();
        publish();
    });

    try {
        const media = window.matchMedia('(display-mode: standalone)');
        const onChange = () => {
            state.standalone = isStandaloneDisplay(window);
            publish();
        };
        if (typeof media.addEventListener === 'function') media.addEventListener('change', onChange);
        else if (typeof media.addListener === 'function') media.addListener(onChange);
    } catch (error) {
        /* matchMedia без поддержки display-mode — состояние просто не
           обновится на ходу, начальное значение уже посчитано. */
    }

    const startWorker = () => {
        if (withServiceWorker) registerServiceWorker(baseUrl);
        else unregisterServiceWorkers();
    };
    /* После load: регистрация воркера — это ещё один запрос и разбор файла,
       и делать его в гонке с первой отрисовкой портала незачем. */
    if (document.readyState === 'complete') startWorker();
    else window.addEventListener('load', startWorker, { once: true });
};
