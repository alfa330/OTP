/*
 * Загрузка материалов «Продакшна»: из браузера прямо в облачное хранилище.
 *
 * ПУТЬ ФАЙЛА. Сервер открывает в хранилище «сессию докачки» и отдаёт её адрес
 * (task_production/storage.py), браузер шлёт файл ОДНИМ запросом PUT на этот
 * адрес — файл читается с диска потоком, в память целиком не ложится. Оборвалась
 * связь — спрашиваем у сервера, сколько байт хранилище уже приняло, и шлём
 * остаток с этого места (Content-Range). Кусками по 8 МБ не режем намеренно:
 * ответ хранилища на промежуточный кусок (308 с заголовком Range) браузер со
 * страницы другого сайта прочитать может не всегда, а сервер — всегда, так что
 * точку продолжения узнаём у него и только после обрыва, а не на каждом куске.
 *
 * ЗАКРЫЛИ ВКЛАДКУ. Сессия живёт неделю. Выбрали тот же файл (имя и размер) к
 * той же задаче — сервер вернёт ту же сессию с её точкой, и загрузка продолжится.
 *
 * КОНТРОЛЬНАЯ СУММА. Пока файл едет, браузер считает его CRC32C (crc32c.js)
 * отдельным проходом чтения; при завершении сервер сверяет её с суммой, которую
 * посчитало само хранилище, и только тогда кладёт файл в раздел.
 *
 * ОЧЕРЕДЬ ОДНА НА СТРАНИЦУ. Файлы идут по одному: два ролика параллельно делят
 * канал и оба приходят позже. Очередь живёт в модуле, а не в окне задачи:
 * закрыли карточку или ушли в другой раздел — загрузка продолжается, а
 * переход со страницы спросит подтверждение.
 */

import axios from 'axios';
import { createCrc32c } from './crc32c';
import { canCapturePoster, classifyFile, retryDelayMs, sessionOutcome, speedOf } from './productionMeta';

const READ_SLICE = 8 * 1024 * 1024;
const MAX_RETRIES = 12;
/* Связь пропала молча (Wi-Fi ушёл, а TCP ещё ждёт): браузер может минутами
   держать запрос «живым», и полоса стоит на месте с «меньше минуты». Сторож
   смотрит на прогресс: 15 секунд тишины — честно пишем «Нет связи», 40 секунд
   или система сообщила об обрыве — обрываем запрос сами и продолжаем с того
   места, которое назовёт сервер. */
const STALL_WARN_MS = 15000;
const STALL_ABORT_MS = 40000;
// После отправки последнего байта хранилище собирает объект — прогресса нет, и это не обрыв.
const FINALIZE_WAIT_MS = 180000;
const POSTER_MAX_SIDE = 800;
const POSTER_TIMEOUT_MS = 8000;

let items = [];
let nextKey = 1;
let running = false;
const listeners = new Set();
/* Строки очереди неизменяемы: каждая правка (`patch`) кладёт НОВЫЙ объект, иначе
   React не увидел бы изменения. Поэтому живой запрос и отметку «отменено» в строке
   не хранить — ссылка на них осталась бы в старой копии, и «Отменить» не оборвал бы
   уже идущую отправку. Они живут здесь, по ключу строки. */
const runtime = new Map();
const live = (key) => {
    if (!runtime.has(key)) runtime.set(key, { xhr: null, canceled: false });
    return runtime.get(key);
};
const forget = (key) => runtime.delete(key);

const emit = () => {
    items = items.slice();
    listeners.forEach((listener) => {
        try { listener(); } catch { /* слушатель не должен ронять очередь */ }
    });
};

const patch = (key, changes) => {
    const index = items.findIndex((item) => item.key === key);
    if (index < 0) return;
    items[index] = { ...items[index], ...changes };
    emit();
};

const active = () => items.some((item) => ['queued', 'starting', 'uploading', 'verifying', 'waiting'].includes(item.state));

const beforeUnload = (event) => {
    if (!active()) return undefined;
    // Портал сам перезагружает страницу, когда вход не удалось продлить (App.jsx:
    // forceReloadAfterFailedAuthRefresh), и уже стёр вход: «Остаться» оставило бы
    // вкладку без входа с висящей загрузкой. Здесь не спрашиваем — так и так заново.
    if (typeof window !== 'undefined' && window.__otpAuthRefreshReloading) return undefined;
    event.preventDefault();
    // eslint-disable-next-line no-param-reassign
    event.returnValue = '';
    return '';
};
if (typeof window !== 'undefined') window.addEventListener('beforeunload', beforeUnload);

export const uploadStore = {
    subscribe(listener) {
        listeners.add(listener);
        return () => listeners.delete(listener);
    },
    snapshot() {
        return items;
    },
    /** Поставить файлы в очередь. Неподходящие сразу отмечаются ошибкой. */
    add({ taskId, files, apiBaseUrl, headers, onDone }) {
        Array.from(files || []).forEach((file) => {
            const check = classifyFile(file);
            items.push({
                key: nextKey++,
                taskId: Number(taskId),
                file,
                name: file?.name || 'файл',
                size: Number(file?.size || 0),
                kind: check.ok ? check.kind : 'file',
                contentType: check.ok ? check.contentType : '',
                state: check.ok ? 'queued' : 'failed',
                error: check.ok ? '' : check.error,
                loaded: 0,
                eta: null,
                materialId: null,
                ownerId: currentUserId(),
                ctx: { apiBaseUrl, headers, onDone },
            });
        });
        emit();
        pump();
    },
    cancel(key) {
        const item = items.find((entry) => entry.key === key);
        if (!item) return;
        const state = live(key);
        state.canceled = true;
        try { state.xhr?.abort(); } catch { /* уже закрыт */ }
        if (item.materialId) {
            axios.delete(`${item.ctx.apiBaseUrl}/api/production/materials/${item.materialId}`, { headers: item.ctx.headers() })
                .catch(() => {});
        }
        items = items.filter((entry) => entry.key !== key);
        emit();
        pump();
    },
    retry(key) {
        const item = items.find((entry) => entry.key === key);
        if (!item || item.state !== 'failed' || !classifyFile(item.file).ok) return;
        forget(key);
        patch(key, { state: 'queued', error: '', loaded: 0, eta: null });
        pump();
    },
    dismiss(key) {
        forget(key);
        items = items.filter((entry) => entry.key !== key);
        emit();
    },
    hasActive: active,
};

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/* Чья это загрузка. Очередь живёт в модуле, а выход из учётки в портале страницу
   не перезагружает: без этой сверки недогруженный файл продолжил бы ехать уже под
   тем, кто вошёл следом. Пользователя портал держит в localStorage['user'].
   Сменился человек — загрузка тихо останавливается; сессия в хранилище остаётся, и
   прежний владелец докачает файл, выбрав его снова. */
const currentUserId = () => {
    try {
        const raw = typeof window !== 'undefined' ? window.localStorage.getItem('user') : null;
        const id = raw ? Number(JSON.parse(raw)?.id || 0) : 0;
        return Number.isFinite(id) && id > 0 ? id : 0;
    } catch {
        return 0;
    }
};
// Вышел из учётки (никого нет) — тоже смена: файл не должен ехать из пустой вкладки.
const ownerChanged = (item) => Boolean(item?.ownerId && currentUserId() !== item.ownerId);

/** Чьи строки показывать: только того, кто сейчас вошёл. */
export const uploadOwnerId = () => currentUserId();

const waitOnline = () => new Promise((resolve) => {
    if (typeof navigator === 'undefined' || navigator.onLine !== false) {
        resolve();
        return;
    }
    const done = () => {
        window.removeEventListener('online', done);
        resolve();
    };
    window.addEventListener('online', done);
});

const apiError = (error, fallback) => {
    const status = error?.response?.status;
    const message = error?.response?.data?.error;
    const out = new Error(message || fallback);
    out.status = status || 0;
    out.code = error?.response?.data?.code || '';
    return out;
};

/* ── Контрольная сумма ─────────────────────────────────────────────────── */

const computeCrc = async (file, isCanceled) => {
    const sum = createCrc32c();
    for (let offset = 0; offset < file.size; offset += READ_SLICE) {
        if (isCanceled()) return null;
        // eslint-disable-next-line no-await-in-loop
        const buffer = await file.slice(offset, Math.min(file.size, offset + READ_SLICE)).arrayBuffer();
        sum.update(new Uint8Array(buffer));
        // Отдать кадр интерфейсу: 8 МБ считаются за ~5 мс, но подряд их сотни.
        // eslint-disable-next-line no-await-in-loop
        await sleep(0);
    }
    return sum.value();
};

/* ── Обложка: кадр из ролика или уменьшенная картинка ───────────────────── */

const toJpeg = (source, width, height) => new Promise((resolve) => {
    const scale = Math.min(1, POSTER_MAX_SIDE / Math.max(width, height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext('2d');
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(source, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.82);
});

const videoPoster = (file) => new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement('video');
    let settled = false;
    const finish = (result) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        video.removeAttribute('src');
        try { video.load(); } catch { /* noop */ }
        URL.revokeObjectURL(url);
        resolve(result);
    };
    const timer = window.setTimeout(() => finish(null), POSTER_TIMEOUT_MS);
    video.preload = 'metadata';
    video.muted = true;
    video.playsInline = true;
    video.onloadedmetadata = () => {
        const duration = Number.isFinite(video.duration) ? video.duration : 0;
        // Не первый кадр: у роликов он часто чёрный (затемнение в начале).
        const at = duration > 0 ? Math.min(1, duration * 0.1) : 0;
        try { video.currentTime = at; } catch { finish(null); }
    };
    video.onseeked = async () => {
        const width = video.videoWidth;
        const height = video.videoHeight;
        if (!width || !height) {
            finish({ blob: null, duration: Number.isFinite(video.duration) ? video.duration : null });
            return;
        }
        const blob = await toJpeg(video, width, height);
        finish({ blob, width, height, duration: Number.isFinite(video.duration) ? video.duration : null });
    };
    // Кодек не поддержан (HEVC на части Windows): обложки не будет, файл всё равно грузится.
    video.onerror = () => finish(null);
    video.src = url;
});

const imagePoster = (file) => new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    let settled = false;
    const finish = (result) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        URL.revokeObjectURL(url);
        resolve(result);
    };
    const timer = window.setTimeout(() => finish(null), POSTER_TIMEOUT_MS);
    image.onload = async () => {
        const width = image.naturalWidth || 0;
        const height = image.naturalHeight || 0;
        // Огромное полотно браузер может не уместить в canvas — обойдёмся без миниатюры.
        if (!width || !height || width > 12000 || height > 12000) {
            finish(width && height ? { blob: null, width, height } : null);
            return;
        }
        try {
            const blob = await toJpeg(image, width, height);
            finish({ blob, width, height });
        } catch {
            finish({ blob: null, width, height });
        }
    };
    image.onerror = () => finish(null);
    image.src = url;
});

export const capturePoster = async (file, kind, contentType) => {
    if (typeof document === 'undefined' || !canCapturePoster(kind, contentType)) return null;
    try {
        return kind === 'video' ? await videoPoster(file) : await imagePoster(file);
    } catch {
        return null;
    }
};

/* ── Отправка в сессию ─────────────────────────────────────────────────── */

const putRemainder = (item, sessionUrl, offset) => new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    const state = live(item.key);
    state.xhr = xhr;
    xhr.open('PUT', sessionUrl, true);
    xhr.setRequestHeader('Content-Range', `bytes ${offset}-${item.size - 1}/${item.size}`);
    const samples = [{ t: Date.now(), loaded: offset }];
    let lastEmit = 0;
    let lastProgress = Date.now();
    let allSent = false;
    let stalled = false;
    let warned = false;
    const abortStalled = () => {
        if (stalled) return;
        stalled = true;
        try { xhr.abort(); } catch { /* уже закрыт */ }
    };
    const watchdog = setInterval(() => {
        if (ownerChanged(item)) {
            // Вошёл другой человек — это не обрыв, а конец этой загрузки на этой вкладке.
            state.canceled = true;
            try { xhr.abort(); } catch { /* уже закрыт */ }
            return;
        }
        const idle = Date.now() - lastProgress;
        if (allSent) {
            if (idle > FINALIZE_WAIT_MS) abortStalled();
            return;
        }
        if (idle > STALL_WARN_MS && !warned) {
            warned = true;
            patch(item.key, { state: 'waiting', eta: null });
        }
        if (idle > STALL_ABORT_MS || (typeof navigator !== 'undefined' && navigator.onLine === false)) abortStalled();
    }, 2000);
    const onOffline = () => abortStalled();
    if (typeof window !== 'undefined') window.addEventListener('offline', onOffline);
    const finish = (result) => {
        clearInterval(watchdog);
        if (typeof window !== 'undefined') window.removeEventListener('offline', onOffline);
        if (state.xhr === xhr) state.xhr = null;
        resolve(result);
    };
    xhr.upload.onload = () => { allSent = true; lastProgress = Date.now(); };
    xhr.upload.onprogress = (event) => {
        const loaded = offset + (event.loaded || 0);
        const now = Date.now();
        lastProgress = now;
        warned = false;
        samples.push({ t: now, loaded });
        while (samples.length > 2 && now - samples[0].t > 8000) samples.shift();
        if (now - lastEmit < 250) return;
        lastEmit = now;
        const speed = speedOf(samples, now);
        patch(item.key, {
            state: 'uploading',
            loaded: Math.min(loaded, item.size),
            eta: speed > 0 ? (item.size - loaded) / speed : null,
        });
    };
    xhr.onload = () => finish({ status: xhr.status });
    xhr.onerror = () => finish({ status: 0 });
    xhr.ontimeout = () => finish({ status: 0 });
    // Обрыв сторожем — это «нет связи» (повторим), а не отмена человеком.
    xhr.onabort = () => finish({ status: stalled ? 0 : -1 });
    // Пустой тип у куска: иначе браузер подставил бы Content-Type файла, а тип
    // объекта уже задан при открытии сессии.
    xhr.send(item.file.slice(offset, item.size, ''));
});

const uploadOne = async (item) => {
    const { apiBaseUrl, headers } = item.ctx;
    const isCanceled = () => ownerChanged(item)
        || live(item.key).canceled
        || !items.some((entry) => entry.key === item.key);
    patch(item.key, { state: 'starting', error: '' });

    let started;
    try {
        const response = await axios.post(
            `${apiBaseUrl}/api/production/tasks/${item.taskId}/uploads`,
            // Отпечаток (дата изменения файла): докачка подхватит только тот же файл.
            { name: item.name, size: item.size, fingerprint: String(item.file?.lastModified || '') },
            { headers: headers() },
        );
        started = response.data || {};
    } catch (error) {
        throw apiError(error, 'Не удалось начать загрузку');
    }
    const materialId = started.material_id;
    patch(item.key, { materialId, loaded: Number(started.offset || 0) });
    if (isCanceled()) {
        // Отменили, пока сервер открывал сессию: заготовку на сервере убираем сразу.
        axios.delete(`${apiBaseUrl}/api/production/materials/${materialId}`, { headers: headers() }).catch(() => {});
        return 'canceled';
    }

    const crcPromise = computeCrc(item.file, isCanceled).catch(() => null);
    const posterPromise = capturePoster(item.file, item.kind, item.contentType).then(async (poster) => {
        if (poster?.blob && !isCanceled()) {
            const form = new FormData();
            form.append('file', poster.blob, 'poster.jpg');
            await axios.post(`${apiBaseUrl}/api/production/uploads/${materialId}/poster`, form, { headers: headers() })
                .catch(() => {});
        }
        return poster;
    });

    let offset = Number(started.offset || 0);
    let finished = Boolean(started.finished);
    // Попытки ПОДРЯД без продвижения: на телефоне каждая блокировка экрана — обрыв,
    // и счёт на весь файл уронил бы долгую загрузку, которая честно шла вперёд.
    let attempt = 0;
    let progressMark = offset;
    while (!finished) {
        if (isCanceled()) return 'canceled';
        // eslint-disable-next-line no-await-in-loop
        const { status } = await putRemainder(item, started.session_url, offset);
        if (status === -1 || isCanceled()) return 'canceled';
        const outcome = sessionOutcome(status);
        if (outcome === 'done') {
            finished = true;
            break;
        }
        if (outcome === 'expired') {
            const expired = new Error('Загрузка устарела — начните её заново');
            expired.code = 'expired';
            throw expired;
        }
        if (outcome === 'fail') throw new Error('Хранилище не приняло файл');
        attempt += 1;
        if (attempt > MAX_RETRIES) throw new Error('Связь с хранилищем прерывается — попробуйте ещё раз');
        patch(item.key, { state: 'waiting', eta: null });
        // eslint-disable-next-line no-await-in-loop
        await waitOnline();
        // eslint-disable-next-line no-await-in-loop
        await sleep(retryDelayMs(attempt));
        if (isCanceled()) return 'canceled';
        try {
            // eslint-disable-next-line no-await-in-loop
            const state = await axios.get(`${apiBaseUrl}/api/production/uploads/${materialId}`, { headers: headers() });
            offset = Number(state.data?.offset || 0);
            finished = Boolean(state.data?.finished);
            if (offset > progressMark) {
                progressMark = offset;
                attempt = 0;
            }
            patch(item.key, { state: 'uploading', loaded: offset });
        } catch (error) {
            if (error?.response?.status === 410) {
                const expired = new Error('Загрузка устарела — начните её заново');
                expired.code = 'expired';
                throw expired;
            }
            // Сервер не ответил — попробуем снова на следующем круге с той же точки.
        }
    }

    patch(item.key, { state: 'verifying', loaded: item.size, eta: null });
    const [crc, poster] = await Promise.all([crcPromise, posterPromise]);
    if (isCanceled()) return 'canceled';
    try {
        await axios.post(`${apiBaseUrl}/api/production/uploads/${materialId}/complete`, {
            crc32c: crc == null ? null : String(crc),
            duration: poster?.duration ?? null,
            width: poster?.width ?? null,
            height: poster?.height ?? null,
        }, { headers: headers() });
    } catch (error) {
        throw apiError(error, 'Не удалось проверить файл');
    }
    return 'done';
};

async function pump() {
    if (running) return;
    running = true;
    try {
        for (;;) {
            // Строки другого человека (вошёл следом в этой же вкладке) из очереди убираем.
            if (items.some(ownerChanged)) {
                items = items.filter((entry) => !ownerChanged(entry));
                emit();
            }
            const item = items.find((entry) => entry.state === 'queued');
            if (!item) break;
            try {
                // eslint-disable-next-line no-await-in-loop
                const outcome = await uploadOne(item);
                if (outcome === 'canceled') {
                    // Отменил человек (строки уже нет) или вошёл другой — строку убираем.
                    forget(item.key);
                    if (items.some((entry) => entry.key === item.key)) {
                        items = items.filter((entry) => entry.key !== item.key);
                        emit();
                    }
                } else if (items.some((entry) => entry.key === item.key)) {
                    patch(item.key, { state: 'done' });
                    forget(item.key);
                    try { item.ctx.onDone?.(item.taskId, item); } catch { /* noop */ }
                    // Готовые строки уходят сами: файл уже виден в списке материалов.
                    setTimeout(() => uploadStore.dismiss(item.key), 1500);
                }
            } catch (error) {
                if (ownerChanged(item)) {
                    forget(item.key);
                    items = items.filter((entry) => entry.key !== item.key);
                    emit();
                } else if (items.some((entry) => entry.key === item.key)) {
                    // Устаревшую загрузку сервер уже забыл: повтор начнёт её с нуля.
                    patch(item.key, { state: 'failed', error: error?.message || 'Загрузка не удалась', eta: null, materialId: null });
                    try { item.ctx.onDone?.(item.taskId, item); } catch { /* noop */ }
                }
            }
        }
    } finally {
        running = false;
    }
}
