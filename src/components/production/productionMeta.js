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

/* Вкладка «Ролики» — только ролики: о них владелец и просил («просто загружать ролики»). */
export const VIDEO_ACCEPT_ATTRIBUTE = Object.entries(PRODUCTION_FILE_TYPES)
    .filter(([, [kind]]) => kind === 'video')
    .map(([ext]) => `.${ext}`)
    .join(',');

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

/** То же для «Роликов»: не ролик — отказ сразу, до загрузки. */
export const classifyLibraryFile = (file) => {
    const check = classifyFile(file);
    if (check.ok && check.kind !== 'video') {
        return { ok: false, error: 'Во вкладку «Ролики» загружаются только ролики: MP4, MOV, WebM, MKV или AVI' };
    }
    return check;
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

export const formatDateTime = (value, now = new Date()) => {
    const date = toDate(value);
    if (!date) return '';
    const time = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
    return `${formatShortDate(date, now)}, ${time}`;
};

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
