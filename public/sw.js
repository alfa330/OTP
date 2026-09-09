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
 * выход. У портала фронт лежит на GitHub Pages, и он уже наступал на устаревшую
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

/* Версию поднимаем руками, когда меняется стратегия: старые кэши чистятся в
   activate по несовпадению имени. */
const VERSION = 'v1';
const SHELL_CACHE = 'icore-shell-' + VERSION;
const ASSET_CACHE = 'icore-assets-' + VERSION;
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

/* Потолок кэша сборочных ассетов. Каждая публикация приносит около сотни новых
   файлов со свежими хэшами, старые из кэша сами не уходят: без потолка кэш рос
   бы с каждым деплоем до конца жизни устройства. */
const ASSET_CACHE_LIMIT = 220;

/* Ответ сюда передаётся УЖЕ КЛОНИРОВАННЫМ, на месте получения. Клон, снятый
   позже (внутри async-функции, после первого await), может опоздать: страница к
   этому моменту уже читает тело, а у прочитанного ответа clone() бросает
   исключение — и в кэш молча не попадало бы ничего. */
const putSafely = async (cacheName, request, response) => {
    /* Кладём только полноценные ответы. opaque (cross-origin без CORS) кладётся
       молча, но читается как ошибка сети, а 4xx/5xx закрепили бы поломку. */
    if (!response || !response.ok || response.type === 'opaque') return;
    const cache = await caches.open(cacheName);
    await cache.put(request, response);
};

const trimCache = async (cacheName, limit) => {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    if (keys.length <= limit) return;
    /* keys() отдаёт записи в порядке добавления — вычищаем самые давние. */
    await Promise.all(keys.slice(0, keys.length - limit).map((key) => cache.delete(key)));
};

/* Гонка, а не AbortController: чтобы отменять, запрос пришлось бы пересобрать
   (`new Request(request, { signal })`), а пересборка запроса-навигации по
   спецификации меняет ему mode с "navigate" на "same-origin" — вместе с этим
   теряется штатная обработка перенаправлений, а GitHub Pages перенаправляет
   `/OTP` на `/OTP/`. Проигравший гонку fetch дозагрузится сам и никому не
   помешает. */
const fetchWithTimeout = (request, timeoutMs) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('network timeout')), timeoutMs);
    fetch(request).then(resolve, reject).finally(() => clearTimeout(timer));
});

/* Документ: сеть → свежая копия в кэш → при обрыве кэш → страница «нет связи».
   В кэше держим только две точки входа (приложение и «Журнал оценок»), а любой
   внутренний адрес вида /wiki/... в офлайне отдаём оболочкой приложения —
   маршрут разберёт роутер уже в браузере. */
const handleNavigation = async (event) => {
    const request = event.request;
    const path = new URL(request.url).pathname;
    const isEntryPoint = path === CALL_EVALUATION_URL;
    const shellKey = isEntryPoint ? CALL_EVALUATION_URL : SHELL_URL;
    try {
        const response = await fetchWithTimeout(request, NAVIGATION_TIMEOUT_MS);
        const copy = response.ok ? response.clone() : null;
        event.waitUntil((async () => {
            /* Ключ канонический: адрес мог приехать с ?v= от восстановления
               после устаревшей сборки, и такие метки в кэше не нужны. */
            await putSafely(SHELL_CACHE, shellKey, copy);
            await trimCache(ASSET_CACHE, ASSET_CACHE_LIMIT);
        })());
        return response;
    } catch (error) {
        const cached = await caches.match(shellKey);
        if (cached) return cached;
        const offline = await caches.match(OFFLINE_URL);
        if (offline) return offline;
        throw error;
    }
};

/* Сборочные ассеты Vite: имя содержит хэш содержимого, значит файл неизменен и
   его можно отдавать из кэша не спрашивая сеть. */
const isBuildAsset = (url) => url.pathname.startsWith(SCOPE_PATH + 'assets/');

const cacheFirst = async (event) => {
    const cached = await caches.match(event.request);
    if (cached) return cached;
    const response = await fetch(event.request);
    const copy = response.ok ? response.clone() : null;
    event.waitUntil(putSafely(ASSET_CACHE, event.request, copy));
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
    const cached = await caches.match(event.request);
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
        const cache = await caches.open(SHELL_CACHE);
        /* Поштучно, а не addAll: тот падает целиком из-за одного неудачного
           файла, и установка воркера сорвалась бы вся. */
        await Promise.all([SHELL_URL, OFFLINE_URL].map(async (url) => {
            try {
                const response = await fetch(url, { cache: 'reload' });
                if (response.ok) await cache.put(url, response);
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
        const names = await caches.keys();
        await Promise.all(
            names
                .filter((name) => name.startsWith('icore-') && !KEEP_CACHES.includes(name))
                .map((name) => caches.delete(name))
        );
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
