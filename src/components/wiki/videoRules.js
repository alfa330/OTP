/* Ролики вики: правила файла и рамка ролика — без React, очереди и сети.
 *
 * Отдельный модуль ради проверки: node --test берёт его целиком (tests/
 * wiki_videos.test.mjs), а модуль загрузки тянет за собой очередь «Продакшна»,
 * которую node без сборщика не разрешит. Правила — те же, что на сервере
 * (wiki/videos.py): MP4, MOV, WebM до 2 ГБ.
 */

export const MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024;

/* Расширение → тип. Тот же список, что VIDEO_TYPES в wiki/videos.py; разойдись
   они — файл, который пропустил браузер, отказал бы сервер (или наоборот), и
   человек узнал бы об этом через минуту загрузки. Паритет сторожит тест. */
/* video-types:start */
export const VIDEO_TYPES = {
    mp4: 'video/mp4',
    m4v: 'video/mp4',
    mov: 'video/quicktime',
    webm: 'video/webm',
};
/* video-types:end */

/* Ролики, которые браузер не сыграет. Отличаем от «вообще не ролика», чтобы
   сказать человеку, что делать: пересохранить в MP4, а не искать другой файл. */
const UNPLAYABLE = new Set(['mkv', 'avi', 'wmv', 'flv', 'mpg', 'mpeg', '3gp']);

/* Для <input accept>: и расширения, и типы — macOS для .mov отдаёт то тип, то
   пустую строку, и окно выбора без расширений прятало бы такие файлы. */
export const VIDEO_ACCEPT = [
    ...Object.keys(VIDEO_TYPES).map((ext) => `.${ext}`),
    'video/mp4', 'video/quicktime', 'video/webm',
].join(',');

export const extensionOf = (name) => {
    const base = String(name || '').split(/[\\/]/).pop();
    const dot = base.lastIndexOf('.');
    return dot > 0 ? base.slice(dot + 1).trim().toLowerCase() : '';
};

/** Похоже ли на ролик вообще — чтобы перетаскивание отличало ролик от картинки. */
export const looksLikeVideo = (file) => {
    const ext = extensionOf(file?.name);
    return Boolean(VIDEO_TYPES[ext] || UNPLAYABLE.has(ext)
        || String(file?.type || '').toLowerCase().startsWith('video/'));
};

/** { ok, error } — те же отказы и те же слова, что у сервера. */
export const checkVideoFile = (file) => {
    const ext = extensionOf(file?.name);
    if (UNPLAYABLE.has(ext)) {
        return { ok: false, error: 'Такой ролик браузер не покажет — сохраните его в MP4 и загрузите ещё раз' };
    }
    if (!VIDEO_TYPES[ext]) return { ok: false, error: 'Это не ролик: подходят MP4, MOV и WebM' };
    const size = Number(file?.size || 0);
    if (size <= 0) return { ok: false, error: 'Файл пустой' };
    if (size > MAX_VIDEO_BYTES) return { ok: false, error: 'Ролик больше 2 ГБ' };
    return { ok: true, ext, contentType: VIDEO_TYPES[ext] };
};

/** Название статьи из имени файла — как title_from_name на сервере. */
export const titleFromName = (name) => {
    let stem = String(name || '').split(/[\\/]/).pop();
    const dot = stem.lastIndexOf('.');
    if (dot > 0) stem = stem.slice(0, dot);
    return stem.replace(/_/g, ' ').split(/\s+/).filter(Boolean).join(' ').slice(0, 200);
};

/* Ручки вики для общей очереди. Ответы ручек повторяют поля «Продакшна»
   (material_id, session_url, offset, finished) — очередь читает именно их. */
export const wikiVideoEndpoints = (base) => ({
    start: `${base}/videos`,
    state: (id) => `${base}/videos/${id}`,
    poster: (id) => `${base}/videos/${id}/poster`,
    complete: (id) => `${base}/videos/${id}/complete`,
    cancel: (id) => `${base}/videos/${id}`,
});

/* ── Как ролик стоит в статье ───────────────────────────────────────────── */

/* Сколько высоты экрана может занять ролик. Вертикальный кадр с телефона во всю
   ширину колонки вышел бы на полтора экрана, а горизонтальный на мониторе —
   на 80 % окна (замер стенда 18.11: колонка 1352 px → кадр 761 px при окне
   956 px): пульт плеера упирался бы в низ экрана. Предел общий для обоих. */
export const MAX_VIDEO_HEIGHT_VH = 72;

/**
 * Рамка ролика: пропорция из размера кадра — чтобы место под ролик стояло
 * сразу, до загрузки обложки (иначе страница прыгает на высоту кадра), — и
 * ширина, при которой кадр не выше MAX_VIDEO_HEIGHT_VH экрана. Без размера
 * кадра — 16:9: так выглядит большинство роликов.
 */
export const videoBox = (width, height) => {
    const w = Number(width);
    const h = Number(height);
    const known = Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0;
    const ratioW = known ? Math.round(w) : 16;
    const ratioH = known ? Math.round(h) : 9;
    return {
        aspectRatio: `${ratioW} / ${ratioH}`,
        width: `min(100%, calc(${MAX_VIDEO_HEIGHT_VH}vh * ${ratioW} / ${ratioH}))`,
    };
};

/** Тот же стиль строкой — для HTML статьи на витрине. */
export const videoBoxCss = (width, height) => {
    const box = videoBox(width, height);
    return `aspect-ratio: ${box.aspectRatio}; width: ${box.width}`;
};

/* ── Адрес ролика в разметке ──────────────────────────────────────────────
   В тексте статьи у ролика ровно один вид адреса: файл вики, /api/wiki/file/<id>
   (в редакторе — раскрытый до домена API). Ролик, вставленный копированием со
   страницы читателя, несёт подписанный адрес хранилища, а номер файла — в
   data-file-id (WikiArticle.jsx: prepareVideos). Такой адрес восстанавливаем
   по номеру; чужой адрес не берём вовсе — санитайзер его выбросил бы, и
   статья ушла бы без ролика молча. */
const FILE_REF = /\/api\/wiki\/file\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:$|[?#])/i;
const FILE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Адрес для узла ролика: свой как есть, восстановленный по номеру или null. */
export const videoAddress = (value, fileId) => {
    const text = String(value || '').trim();
    if (FILE_REF.test(text)) return text;
    const id = String(fileId || '').trim();
    return FILE_ID.test(id) ? `/api/wiki/file/${id.toLowerCase()}` : null;
};

/** Номер файла в адресе ролика (или null). */
export const videoFileId = (value) => {
    const found = FILE_REF.exec(String(value || ''));
    return found ? found[1].toLowerCase() : null;
};
