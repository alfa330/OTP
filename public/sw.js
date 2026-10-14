/* Сервис-воркер портала — то, из-за чего значок на домашнем экране становится
 * приложением, а не закладкой.
 *
 * Зачем он вообще. Chrome предлагает «Установить» только сайту, у которого есть
 * манифест И зарегистрированный воркер с обработчиком fetch: без этой пары
 * событие beforeinstallprompt не приходит, и кнопки установки на Android не
 * будет. iOS воркера не требует, там добавление на экран «Домой» живёт в меню
 * «Поделиться», — но и там воркер полезен: запуск с домашнего экрана без сети
 * иначе показывает системную страницу «нет интернета».
 *
 * ГЛАВНОЕ ПРАВИЛО ЭТОГО ФАЙЛА: документ берётся ИЗ СЕТИ, кэш — только запасной
 * выход: при обрыве и когда сеть молчит дольше 2 с, а сохранённая копия есть
 * (NAVIGATION_CACHED_WAIT_MS). У портала фронт лежит на GitHub Pages, и он уже наступал на устаревшую
 * сборку: публикация Pages иногда не доезжает, у людей остаётся старый бандл, и
 * приложение падает на ChunkLoadError (для этого в src/staleBundleRecovery.js
 * живёт перезагрузка с ?v=). Воркер, который отдавал бы index.html из кэша,
 * превратил бы редкую беду в постоянную: старая сборка залипла бы навсегда, и
 * никакой деплой её бы не сдвинул. Поэтому cache-first здесь позволен ровно
 * одному классу файлов — сборочным ассетам с хэшем в имени (assets/*), где имя
 * файла меняется вместе с содержимым и устареть физически не может.
 *
 * Чего воркер НЕ трогает вовсе:
 *   * чужое происхождение — вся работа с API идёт на otp-2-fos4.onrender.com,
 *     и кэшированный ответ сервера означал бы показанные не те данные;
 *   * `/api/...` своего происхождения — на локальном стенде Flask раздаёт и
 *     фронт, и API с одного адреса, там первое правило не сработало бы;
 *   * не-GET и запросы с Range — так тянутся записи разговоров, кусками.
 */

/* Оболочка обновляется отдельно. Исправные хэшированные файлы нужны вкладкам
   предыдущей сборки даже после обновления воркера. */
const SHELL_CACHE = 'icore-shell-v2';
const ASSET_CACHE = 'icore-assets-v1';
const KEEP_CACHES = [SHELL_CACHE, ASSET_CACHE];

/* Порталу отведён путь установки: '/' на своём домене, '/OTP/' на GitHub Pages.
   Всё, что вне его, воркеру не принадлежит. */
const SCOPE_PATH = new URL(self.registration.scope).pathname;
const SHELL_URL = SCOPE_PATH;                          // index.html приложения
const OFFLINE_URL = SCOPE_PATH + 'offline.html';
const CALL_EVALUATION_URL = SCOPE_PATH + 'call_evaluation.html';

/* Сеть иногда не отвечает и не обрывается — телефон в лифте, отельный Wi-Fi с
   порталом авторизации. Без таймаута человек смотрел бы в белый экран, пока
   браузер сам не сдастся. */
const NAVIGATION_TIMEOUT_MS = 7000;

/* Если сохранённая копия страницы есть, сеть ждём не дольше 2 с, а потом
   открываем копию (решение владельца 14.10.2026, жалоба «с иконки грузится
   слишком долго»: на плохой связи запуск стоял по 7 с). Обычный ответ
   GitHub Pages с телефона — 0,4–0,8 с, поэтому на нормальной связи страница
   по-прежнему всегда свежая. Документ, пришедший позже, всё равно ложится в
   кэш — к следующему запуску. */
const NAVIGATION_CACHED_WAIT_MS = 2000;

/* Предзагрузка навигации начинает запрос страницы, пока воркер ещё
   просыпается (при запуске с иконки он спит). Но идёт она через HTTP-кэш
   браузера, а Pages отдаёт страницу с max-age=600: ответ, чей Date старше
   минуты, взят из этого кэша, и его заменяет запрос мимо кэша. */
const PRELOAD_FRESH_MS = 60 * 1000;

/* Потолок кэша сборочных ассетов. Каждая публикация приносит около сотни новых
   файлов со свежими хэшами, старые из кэша сами не уходят: без потолка кэш рос
   бы с каждым деплоем до конца жизни устройства. */
const ASSET_CACHE_LIMIT = 220;

/* CacheStorage может быть недоступен или повреждён. Его ошибка не должна
   превращать доступный по сети JS-модуль в Failed to fetch. */
const matchSafely = async (cacheName, request) => {
    try {
        return await caches.match(request, { cacheName });
    } catch (error) {
        return undefined;
    }
};

/* Ответ сюда передаётся УЖЕ КЛОНИРОВАННЫМ, на месте получения. Клон, снятый
   позже (внутри async-функции, после первого await), может опоздать: страница к
   этому моменту уже читает тело, а у прочитанного ответа clone() бросает
   исключение — и в кэш молча не попадало бы ничего. */
const putSafely = async (cacheName, request, response) => {
    /* Кладём только полноценные ответы. opaque (cross-origin без CORS) кладётся
       молча, но читается как ошибка сети, а 4xx/5xx закрепили бы поломку. */
    if (!response || !response.ok || response.type === 'opaque') return;
    try {
        const cache = await caches.open(cacheName);
        await cache.put(request, response);
    } catch (error) {
        /* Квота и запрет хранилища не мешают отдать сетевой ответ. */
    }
};

const trimCache = async (cacheName, limit) => {
    try {
        const cache = await caches.open(cacheName);
        const keys = await cache.keys();
        if (keys.length <= limit) return;
        /* keys() отдаёт записи в порядке добавления — вычищаем самые давние. */
        await Promise.all(keys.slice(0, keys.length - limit).map((key) => cache.delete(key)));
    } catch (error) {
        /* Уборка кэша — необязательная фоновая работа. */
    }
};

/* Обычный fetch(request) тоже читает HTTP-кэш браузера. Документ всегда
   запрашиваем заново, иначе он может ссылаться на уже удалённые чанки.
   Предзагрузку берём, только если она свежая (см. PRELOAD_FRESH_MS). */
const readNetworkDocument = async (event) => {
    try {
        const preloaded = await event.preloadResponse;
        if (preloaded) {
            const servedAt = Date.parse(preloaded.headers.get('date') || '');
            if (Number.isFinite(servedAt) && Math.abs(Date.now() - servedAt) <= PRELOAD_FRESH_MS) {
                return preloaded;
            }
        }
    } catch (error) {
        /* Предзагрузка сорвалась — обычный запрос ниже. */
    }
    return fetch(event.request, { cache: 'no-store' });
};

/* Документ: сеть → свежая копия в кэш → при обрыве (или через 2 с, если копия
   есть) кэш → страница «нет связи». В кэше держим только две точки входа
   (приложение и «Журнал оценок»), а любой внутренний адрес вида /wiki/... в
   офлайне отдаём оболочкой приложения — маршрут разберёт роутер уже в браузере. */
const handleNavigation = async (event) => {
    const request = event.request;
    const path = new URL(request.url).pathname;
    const isEntryPoint = path === CALL_EVALUATION_URL;
    const shellKey = isEntryPoint ? CALL_EVALUATION_URL : SHELL_URL;

    /* Копию снимаем сразу по приходе ответа — до того, как его начнёт читать
       страница (см. putSafely), — и кладём, когда бы ответ ни пришёл: в том
       числе если страница уже открылась сохранённой копией. */
    const network = readNetworkDocument(event)
        .then((response) => ({ response, copy: response.ok ? response.clone() : null }));
    event.waitUntil(network
        .then(async ({ copy }) => {
            /* Ключ канонический: адрес мог приехать с ?v= от восстановления
               после устаревшей сборки, и такие метки в кэше не нужны. */
            await putSafely(SHELL_CACHE, shellKey, copy);
            await trimCache(ASSET_CACHE, ASSET_CACHE_LIMIT);
        })
        .catch(() => {}));

    const cached = await matchSafely(SHELL_CACHE, shellKey);
    let timer = null;
    try {
        return await Promise.race([
            network.then(({ response }) => response),
            new Promise((resolve, reject) => {
                timer = setTimeout(
                    () => reject(new Error('network timeout')),
                    cached ? NAVIGATION_CACHED_WAIT_MS : NAVIGATION_TIMEOUT_MS
                );
            }),
        ]);
    } catch (error) {
        if (cached) return cached;
        const offline = await matchSafely(SHELL_CACHE, OFFLINE_URL);
        if (offline) return offline;
        throw error;
    } finally {
        clearTimeout(timer);
    }
};

/* Сборочные ассеты Vite: имя содержит хэш содержимого, значит файл неизменен и
   его можно отдавать из кэша не спрашивая сеть. */
const isBuildAsset = (url) => url.pathname.startsWith(SCOPE_PATH + 'assets/');

const isValidBuildAsset = (request, response) => {
    if (!response || !response.ok || response.type === 'opaque') return false;
    const path = new URL(request.url).pathname;
    const type = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
    if (/\.(?:m?js)$/i.test(path)) {
        return /^(?:text|application)\/(?:javascript|ecmascript|x-javascript)$/.test(type);
    }
    if (/\.css$/i.test(path)) return type === 'text/css';
    return true;
};

const cacheFirst = async (event) => {
    const request = event.request;
    const bypassCache = ['reload', 'no-cache', 'no-store'].includes(request.cache);
    const cached = bypassCache ? null : await matchSafely(ASSET_CACHE, request);
    if (isValidBuildAsset(request, cached)) return cached;
    if (cached) {
        /* Например, HTML-заглушка с кодом 200 попала в старый кэш под именем
           .js. Не закрепляем её навсегда и обходим также HTTP-кэш. */
        event.waitUntil((async () => {
            try {
                const cache = await caches.open(ASSET_CACHE);
                await cache.delete(request);
            } catch (error) { /* Сеть должна работать и без хранилища. */ }
        })());
    }
    const response = cached
        ? await fetch(request, { cache: 'no-store' })
        : await fetch(request);
    const copy = isValidBuildAsset(request, response) ? response.clone() : null;
    event.waitUntil(putSafely(ASSET_CACHE, request, copy));
    return response;
};

/* Статика из public/ — иконки, favicon, 3D-модель тренажёра, worklet, манифест.
   Хэша в имени у неё нет, поэтому список ЗАКРЫТЫЙ, а не «всё своё
   происхождение»: на локальном стенде Flask раздаёт с того же адреса и фронт, и
   прокси тайлов карты (/map/tile/...), и класть чужие ответы в кэш портала
   незачем — кэш конечен, и тайлы вытеснили бы из него сам портал. */
const PUBLIC_ASSET_RE = /\/(?:icons|models)\/|\/(?:favicon\.ico|trainer-worklet\.js|manifest\.webmanifest|offline\.html)$/;

const isPublicAsset = (url) => PUBLIC_ASSET_RE.test(url.pathname);

/* Отдаём из кэша сразу, а копию обновляем в фоне — так файл после публикации
   обновится к следующему открытию, но не задержит текущее. */
const staleWhileRevalidate = async (event) => {
    const cached = await matchSafely(ASSET_CACHE, event.request);
    const network = fetch(event.request)
        .then((response) => {
            const copy = response.ok ? response.clone() : null;
            event.waitUntil(putSafely(ASSET_CACHE, event.request, copy));
            return response;
        })
        .catch(() => null);
    if (cached) {
        event.waitUntil(network);
        return cached;
    }
    const response = await network;
    if (response) return response;
    throw new Error('offline');
};

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        /* Поштучно, а не addAll: тот падает целиком из-за одного неудачного
           файла, и установка воркера сорвалась бы вся. */
        await Promise.all([SHELL_URL, OFFLINE_URL].map(async (url) => {
            try {
                const response = await fetch(url, { cache: 'reload' });
                await putSafely(SHELL_CACHE, url, response);
            } catch (error) {
                /* Нет сети в момент установки — не беда, оболочка положится
                   в кэш при первом же удачном открытии. */
            }
        }));
        await self.skipWaiting();
    })());
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        try {
            const names = await caches.keys();
            await Promise.all(
                names
                    .filter((name) => name.startsWith('icore-') && !KEEP_CACHES.includes(name))
                    .map((name) => caches.delete(name))
            );
        } catch (error) {
            /* Заблокированное хранилище не должно удерживать старый воркер. */
        }
        /* Запрос страницы — параллельно с пробуждением воркера, см.
           readNetworkDocument. */
        try {
            if (self.registration.navigationPreload) await self.registration.navigationPreload.enable();
        } catch (error) {
            /* Нет поддержки — страница пойдёт обычным запросом из воркера. */
        }
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;
    if (request.headers.has('range')) return;

    let url;
    try {
        url = new URL(request.url);
    } catch (error) {
        return;
    }
    if (url.origin !== self.location.origin) return;
    if (!url.pathname.startsWith(SCOPE_PATH)) return;
    if (url.pathname.startsWith(SCOPE_PATH + 'api/')) return;

    if (request.mode === 'navigate') {
        event.respondWith(handleNavigation(event));
        return;
    }
    if (isBuildAsset(url)) {
        event.respondWith(cacheFirst(event));
        return;
    }
    if (isPublicAsset(url)) {
        event.respondWith(staleWhileRevalidate(event));
        return;
    }
    /* Всё остальное — мимо воркера, как будто его нет. */
});
