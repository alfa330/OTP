/*
 * Раздел «Продакшн» (задача #316): справочник и расчёты без React.
 *
 * Раздел — рабочее место маркетинга поверх задач типа «Продакшн» (#354): доска
 * этих задач, материалы по версиям и «Готовое». Задача та же, что в «Задачах»,
 * поэтому колонки доски — это её статусы, а «отправить на проверку» — обычная
 * сдача.
 *
 * ТЕ ЖЕ ТИПЫ ФАЙЛОВ — в task_production/materials.py (TYPES): сервер принимает
 * только свои, а выбор файла здесь показывает и отсекает заранее, чтобы человек
 * не ждал минуту загрузки ради отказа. tests/test_task_production_materials.py
 * сверяет блок между отметками types:start / types:end с серверным буквально.
 *
 * Модуль целиком проверяет node --test (tests/production_meta.test.mjs).
 */

import { PRODUCTION_ITEM_GROUPS } from '../tasks/productionBrief.js';

export const MAX_MATERIAL_BYTES = 2 * 1024 * 1024 * 1024;
export const MAX_MATERIALS_PER_VERSION = 60;

/* types:start */
export const PRODUCTION_FILE_TYPES = {
    mp4: ['video', 'video/mp4'],
    m4v: ['video', 'video/mp4'],
    mov: ['video', 'video/quicktime'],
    webm: ['video', 'video/webm'],
    mkv: ['video', 'video/x-matroska'],
    avi: ['video', 'video/x-msvideo'],
    jpg: ['image', 'image/jpeg'],
    jpeg: ['image', 'image/jpeg'],
    png: ['image', 'image/png'],
    webp: ['image', 'image/webp'],
    gif: ['image', 'image/gif'],
    svg: ['image', 'image/svg+xml'],
    heic: ['image', 'image/heic'],
    heif: ['image', 'image/heif'],
    tif: ['image', 'image/tiff'],
    tiff: ['image', 'image/tiff'],
    pdf: ['file', 'application/pdf'],
    psd: ['file', 'image/vnd.adobe.photoshop'],
    ai: ['file', 'application/postscript'],
    eps: ['file', 'application/postscript'],
    zip: ['file', 'application/zip'],
    rar: ['file', 'application/vnd.rar'],
    '7z': ['file', 'application/x-7z-compressed'],
    mp3: ['file', 'audio/mpeg'],
    wav: ['file', 'audio/wav'],
    m4a: ['file', 'audio/mp4'],
    aac: ['file', 'audio/aac'],
};
/* types:end */

/* Что браузер нарисует сам: из этих картинок снимаем миниатюру для плитки. */
export const PREVIEW_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/svg+xml']);
/* Из этих роликов браузер может снять кадр — если умеет их кодек. */
export const PLAYABLE_VIDEO_TYPES = new Set(['video/mp4', 'video/quicktime', 'video/webm']);

export const fileExtension = (name) => {
    const base = String(name || '').split(/[\\/]/).pop() || '';
    const dot = base.lastIndexOf('.');
    return dot > 0 ? base.slice(dot + 1).trim().toLowerCase() : '';
};

export const ACCEPT_ATTRIBUTE = Object.keys(PRODUCTION_FILE_TYPES).map((ext) => `.${ext}`).join(',');


/** Годится ли файл, и если нет — почему (тем же текстом, что вернул бы сервер). */
export const classifyFile = (file) => {
    const ext = fileExtension(file?.name);
    const known = PRODUCTION_FILE_TYPES[ext];
    if (!known) {
        return { ok: false, error: 'Такой файл не принимается: подходят ролики, картинки, PDF, PSD, AI, EPS, архивы и звук' };
    }
    const size = Number(file?.size || 0);
    if (size <= 0) return { ok: false, error: 'Файл пустой' };
    if (size > MAX_MATERIAL_BYTES) return { ok: false, error: 'Файл больше 2 ГБ' };
    const [kind, contentType] = known;
    return { ok: true, ext, kind, contentType, size };
};

/* ── «Медиатека»: файлы без задачи ─────────────────────────────────────── */

/* Категории — список владельца 16.10.2026, с его же обычными размерами файлов.
   Баннеры — картинки, остальные — ролики. Тот же список (код, подпись, вид) — в
   task_production/materials.py (LIBRARY_CATEGORIES): сервер принимает только свои
   коды и сверяет вид файла; тест сверяет блок между отметками буквально.
   short — подпись там, где тесно (телефон, меню); note — обычный размер файла. */
/* categories:start */
export const LIBRARY_CATEGORIES = [
    { value: 'banner', label: 'Баннеры', kind: 'image', short: 'Баннеры', note: 'фото · обычно 2–4 МБ' },
    { value: 'animation', label: 'Анимационные ролики', kind: 'video', short: 'Анимация', note: 'обычно 25–30 МБ' },
    { value: 'ad', label: 'Рекламные ролики', kind: 'video', short: 'Реклама', note: 'обычно 60–80 МБ' },
    { value: 'reels', label: 'Рилсы', kind: 'video', short: 'Рилсы', note: 'обычно 120–150 МБ' },
];
/* categories:end */

export const libraryCategory = (value) => LIBRARY_CATEGORIES.find((category) => category.value === value) || null;

/** Вид файла по расширению: 'video', 'image', 'file' или null — такого не принимаем. */
export const fileKindOf = (file) => PRODUCTION_FILE_TYPES[fileExtension(file?.name)]?.[0] || null;

const acceptOfKinds = (kinds) => Object.entries(PRODUCTION_FILE_TYPES)
    .filter(([, [kind]]) => kinds.includes(kind))
    .map(([ext]) => `.${ext}`)
    .join(',');

/** Атрибут accept выбора файлов: картинки для баннеров, ролики для остальных. */
export const libraryAccept = (category) => {
    const target = libraryCategory(category);
    return acceptOfKinds(target ? [target.kind] : ['image', 'video']);
};

const LIBRARY_KIND_ERRORS = {
    image: 'Это не картинка: в «Баннеры» подходят JPG, PNG, WebP, GIF, SVG, HEIC или TIFF',
    video: 'Это не ролик: в эту категорию подходят MP4, MOV, WebM, MKV или AVI',
};

/** Годится ли файл в категорию «Медиатеки» — теми же словами и в том же порядке,
    что у сервера (classify_library): сначала вид, потом размер. Без категории —
    отказ: ролик сам не знает, анимационный он или рекламный. */
export const classifyLibraryFile = (file, category) => {
    const target = libraryCategory(category);
    if (!target) {
        return ['image', 'video'].includes(fileKindOf(file))
            ? { ok: false, error: 'Выберите категорию: баннеры, анимационные ролики, рекламные ролики или рилсы' }
            : { ok: false, error: 'Такой файл в медиатеку не подходит: сюда загружают баннеры (картинки) и ролики' };
    }
    if (fileKindOf(file) !== target.kind) return { ok: false, error: LIBRARY_KIND_ERRORS[target.kind] };
    const check = classifyFile(file);
    return check.ok ? { ...check, category: target.value } : check;
};

export const canCapturePoster = (kind, contentType) => (
    (kind === 'image' && PREVIEW_IMAGE_TYPES.has(contentType))
    || (kind === 'video' && PLAYABLE_VIDEO_TYPES.has(contentType))
);

/* ── Доска ─────────────────────────────────────────────────────────────── */

/* Колонки — статусы задачи. «Возврат» живёт в «В работе», как и в «Задачах»:
   работа снова у исполнителя. Принятое уходит во вкладку «Готовое». */
export const BOARD_COLUMNS = [
    { id: 'todo', title: 'К выполнению', statuses: ['assigned'] },
    { id: 'progress', title: 'В работе', statuses: ['in_progress', 'returned'] },
    { id: 'review', title: 'На проверке', statuses: ['completed'] },
];

export const columnOf = (status) => BOARD_COLUMNS.find((column) => column.statuses.includes(status))?.id || null;

/** Задачи по колонкам, внутри колонки — ближайший срок первым, без срока — в конце. */
export const groupBoard = (tasks) => {
    const out = Object.fromEntries(BOARD_COLUMNS.map((column) => [column.id, []]));
    (tasks || []).forEach((task) => {
        const column = columnOf(task?.status);
        if (column) out[column].push(task);
    });
    const dueKey = (task) => (task?.due_at ? Date.parse(task.due_at) : Number.POSITIVE_INFINITY);
    Object.values(out).forEach((list) => list.sort((a, b) => {
        const left = dueKey(a);
        const right = dueKey(b);
        if (left !== right) return left < right ? -1 : 1;
        return (Date.parse(b?.created_at || 0) || 0) - (Date.parse(a?.created_at || 0) || 0);
    }));
    return out;
};

/* ── «Готовое» ─────────────────────────────────────────────────────────── */

export const DONE_GROUPS = [
    { value: '', label: 'Все' },
    ...PRODUCTION_ITEM_GROUPS.map((group) => ({
        value: group.code,
        label: { print: 'Полиграфия', digital: 'Digital', video: 'Видео', brand: 'Бренд' }[group.code] || group.label,
    })),
];

export const itemGroupOf = (itemCode) => (
    PRODUCTION_ITEM_GROUPS.find((group) => group.items.some((item) => item.code === itemCode))?.code || null
);

/* ── Подписи ───────────────────────────────────────────────────────────── */

export const STATUS_LABELS = {
    assigned: 'К выполнению',
    in_progress: 'В работе',
    returned: 'Возврат',
    completed: 'На проверке',
    accepted: 'Принято',
};

/* Состояние версии в карточке. «working» — собирается сейчас. */
export const VERSION_STATE_LABELS = {
    working: 'в работе',
    review: 'на проверке',
    returned: 'вернули на доработку',
    accepted: 'принята',
    draft: 'не отправлена',
};

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

const toDate = (value) => {
    if (!value) return null;
    const date = value instanceof Date ? value : new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
};

export const formatShortDate = (value, now = new Date()) => {
    const date = toDate(value);
    if (!date) return '';
    const sameYear = date.getFullYear() === now.getFullYear();
    return `${date.getDate()} ${MONTHS_SHORT[date.getMonth()]}${sameYear ? '' : ` ${date.getFullYear()}`}`;
};

export const formatTime = (value) => {
    const date = toDate(value);
    if (!date) return '';
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
};

export const formatDateTime = (value, now = new Date()) => {
    const date = toDate(value);
    if (!date) return '';
    return `${formatShortDate(date, now)}, ${formatTime(date)}`;
};

const MONTHS_FULL = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

/** Заголовок дня в «Истории»: «Сегодня», «Вчера», «14 октября», «3 января 2025». */
export const dayLabel = (value, now = new Date()) => {
    const date = toDate(value);
    if (!date) return '';
    const dayStart = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    // Округление, а не деление нацело: в сутках перевода часов 23 или 25 часов.
    const daysAgo = Math.round((dayStart(now) - dayStart(date)) / 86400000);
    if (daysAgo === 0) return 'Сегодня';
    if (daysAgo === 1) return 'Вчера';
    return `${date.getDate()} ${MONTHS_FULL[date.getMonth()]}${date.getFullYear() === now.getFullYear() ? '' : ` ${date.getFullYear()}`}`;
};

/** События «Истории» по дням — в том порядке, в каком пришли (свежее сверху). */
export const groupByDay = (events, now = new Date()) => {
    const groups = [];
    (events || []).forEach((event) => {
        const label = dayLabel(event.at, now);
        const last = groups[groups.length - 1];
        if (last && last.label === label) last.events.push(event);
        else groups.push({ label, events: [event] });
    });
    return groups;
};

/* Что сделали с файлом. Слово согласовано с «файлом», а не с человеком: род
   сотрудника по имени не угадать. */
export const HISTORY_ACTION_LABELS = { added: 'Добавлен', moved: 'Перенесён', deleted: 'Удалён' };

/** Срок задачи: подпись и просрочка. Сданная и принятая задача не «горит». */
export const dueInfo = (task, now = new Date()) => {
    const due = toDate(task?.due_at);
    if (!due) return null;
    const open = !['completed', 'accepted'].includes(task?.status);
    return { label: formatShortDate(due, now), overdue: open && due.getTime() < now.getTime() };
};

const plural = (n, one, few, many) => {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
    return many;
};

export const filesLabel = (n) => `${n} ${plural(n, 'файл', 'файла', 'файлов')}`;
export const videosLabel = (n) => `${n} ${plural(n, 'ролик', 'ролика', 'роликов')}`;

export const formatBytes = (bytes) => {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} Б`;
    const units = ['КБ', 'МБ', 'ГБ'];
    let amount = value / 1024;
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
        amount /= 1024;
        unit += 1;
    }
    const digits = amount >= 100 ? 0 : 1;
    return `${amount.toFixed(digits).replace('.', ',').replace(/,0$/, '')} ${units[unit]}`;
};

export const formatDuration = (seconds) => {
    const total = Math.round(Number(seconds || 0));
    if (!Number.isFinite(total) || total <= 0) return '';
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const ss = String(s).padStart(2, '0');
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
};

/** «ещё 3 мин» — сколько осталось загружать. Пока скорость не известна — пусто. */
export const formatEta = (seconds) => {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value <= 0) return '';
    if (value < 60) return 'меньше минуты';
    const minutes = Math.round(value / 60);
    if (minutes < 60) return `ещё ${minutes} мин`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `ещё ${hours} ч ${rest} мин` : `ещё ${hours} ч`;
};

export const initialsOf = (name) => String(name || '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() || '')
    .join('') || '·';

/* Короткое имя для карточки: «Фамилия И.» */
export const shortName = (name) => {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (parts.length <= 1) return parts[0] || '';
    return `${parts[0]} ${parts[1][0]}.`;
};

/* ── Загрузка ──────────────────────────────────────────────────────────── */

/** Скорость по скользящему окну: [{t, loaded}] за последние 8 секунд. */
export const speedOf = (samples, now) => {
    const recent = (samples || []).filter((sample) => now - sample.t <= 8000);
    if (recent.length < 2) return 0;
    const first = recent[0];
    const last = recent[recent.length - 1];
    const dt = (last.t - first.t) / 1000;
    return dt > 0 ? Math.max(0, (last.loaded - first.loaded) / dt) : 0;
};

/** Пауза перед повтором после обрыва: 1, 2, 4… до 30 секунд. */
export const retryDelayMs = (attempt) => Math.min(30000, 1000 * (2 ** Math.max(0, attempt - 1)));

/** Ответ на PUT в сессию: что делать дальше. */
export const sessionOutcome = (status) => {
    if (status === 200 || status === 201) return 'done';
    if (status === 404 || status === 410) return 'expired';
    if (status === 308 || status === 0 || status >= 500 || status === 408 || status === 429) return 'retry';
    return 'fail';
};
