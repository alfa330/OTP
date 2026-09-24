/* Картинки во вложениях задачи: что показывать плиткой и как держать адреса.
 *
 * Сервер отдаёт адреса картинок карточки одним запросом
 * (GET /api/tasks/<id>/photos → подписанные ссылки, см. task_photos на
 * сервере). Здесь — правила, общие для всех мест карточки, где лежат файлы:
 * постановка, результат, уточнения. Отдельный модуль, а не часть
 * TasksView.jsx: правило «что считается картинкой» обязано совпадать с
 * серверным (task_photos.PREVIEW_TYPES), и проверить это тестом
 * (tests/task_photos.test.mjs) можно только отдельно от JSX.
 */

/* Что показываем плиткой. Совпадает с task_photos.PREVIEW_TYPES: сервер
   подписывает ровно эти типы, и расхождение дало бы плитку без картинки.
   Новые фото приходят уже в WebP; JPEG и PNG — ради загруженных раньше. */
export const PHOTO_PREVIEW_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

/* Сколько держим ответ сервера. Любая выданная сервером ссылка живёт не меньше
   60 минут (task_photos: SIGNED_MINUTES и RESIGN_BEFORE_MINUTES); 45 — запас,
   чтобы повторно открытая карточка не взяла из памяти уже мёртвый адрес. */
export const PHOTO_PREVIEW_TTL_MS = 45 * 60 * 1000;

const typeOf = (attachment) => String(attachment?.content_type || '')
    .split(';')[0].trim().toLowerCase();

export const isPreviewablePhoto = (attachment) => (
    Number(attachment?.id || 0) > 0 && PHOTO_PREVIEW_TYPES.includes(typeOf(attachment))
);

/* Номера всех картинок карточки — постановки, результата и уточнений, без
   повторов (файл уточнения лежит и в attachments, и в сообщении). По ним
   решается, идти ли на сервер: карточка без картинок запроса не делает. */
export const taskPhotoIds = (task) => {
    const lists = [
        task?.attachments,
        task?.completion_attachments,
        ...(Array.isArray(task?.messages) ? task.messages.map((message) => message?.attachments) : []),
    ];
    const ids = new Set();
    lists.forEach((list) => {
        (Array.isArray(list) ? list : []).forEach((attachment) => {
            if (isPreviewablePhoto(attachment)) ids.add(Number(attachment.id));
        });
    });
    return [...ids].sort((left, right) => left - right);
};

/* Файлы одного блока карточки → картинки плитками и остальное кнопками.
 *
 * `previews` — ответ сервера по номерам, `settled` — ответ уже пришёл (или не
 * пришёл вовсе). Пока ждём, картинка стоит пустой плиткой — место под неё
 * занято, и кнопки файлов не прыгают. Картинка, которой в ответе нет (старый
 * файл в базе, подпись не собралась, <img> не смог её нарисовать), становится
 * обычной кнопкой: пустая плитка выглядела бы поломкой, а файл всё равно можно
 * скачать. */
export const splitTaskFiles = (attachments, previews, settled, broken) => {
    const photos = [];
    const files = [];
    (Array.isArray(attachments) ? attachments : []).forEach((attachment) => {
        const id = Number(attachment?.id || 0);
        const preview = previews?.[id] || null;
        const usable = isPreviewablePhoto(attachment)
            && !broken?.has?.(id)
            && (preview || !settled);
        if (usable) photos.push({ attachment, preview });
        else files.push(attachment);
    });
    return { photos, files };
};

/* Ответы сервера по задачам. Ключ — задача, но попадание засчитывается,
   только если в сохранённом ответе есть КАЖДАЯ картинка, которая нужна сейчас:
   к задаче могли дослать файл уточнением или результатом, и старый ответ
   оставил бы новую картинку без адреса. */
export const createPhotoPreviewCache = (ttlMs = PHOTO_PREVIEW_TTL_MS, maxTasks = 50) => {
    const store = new Map();
    return {
        read(taskId, ids, now) {
            const entry = store.get(Number(taskId));
            if (!entry || now - entry.at > ttlMs) return null;
            return ids.every((id) => entry.known.has(Number(id))) ? entry.byId : null;
        },
        write(taskId, ids, photos, now) {
            const byId = {};
            (Array.isArray(photos) ? photos : []).forEach((photo) => {
                const id = Number(photo?.id || 0);
                if (id && photo?.url) byId[id] = { url: photo.url, thumbUrl: photo.thumb_url || photo.url };
            });
            // «Известны» и те, что сервер не подписал: иначе карточка со старым
            // файлом из базы шла бы на сервер при каждом открытии.
            store.delete(Number(taskId));
            store.set(Number(taskId), { at: now, byId, known: new Set(ids.map(Number)) });
            while (store.size > maxTasks) store.delete(store.keys().next().value);
            return byId;
        },
        drop(taskId) {
            store.delete(Number(taskId));
        },
    };
};
