/* Что умеет просмотр файла и как назвать файл человеку.

   Вид определяется по расширению, затем по типу из базы, а если оба молчат —
   по первым байтам уже скачанного файла (sniffKind). Последнее не прихоть:
   у части старых вложений задач имя без расширения («png», «jpg» после
   secure_filename), а тип — application/octet-stream. */

const GROUPS = {
    pdf: ['pdf'],
    image: ['png', 'jpg', 'jpeg', 'jfif', 'pjpeg', 'gif', 'webp', 'bmp', 'svg', 'avif', 'ico', 'heic', 'heif', 'tif', 'tiff'],
    video: ['mp4', 'webm', 'mov', 'm4v', 'ogv'],
    audio: ['mp3', 'wav', 'ogg', 'oga', 'm4a', 'aac', 'flac', 'opus', 'weba'],
    docx: ['docx', 'docm', 'dotx', 'dotm'],
    xlsx: ['xlsx', 'xlsm', 'xltx', 'xltm'],
    pptx: ['pptx', 'pptm', 'ppsx', 'ppsm', 'potx'],
    csv: ['csv', 'tsv'],
    markdown: ['md', 'markdown'],
    json: ['json', 'geojson'],
    // HTML — макет: окно рисует страницу (htmlMockup.js), исходник — во вкладке «Код».
    html: ['html', 'htm', 'xhtml'],
    text: ['txt', 'text', 'log', 'ini', 'cfg', 'conf', 'env', 'yaml', 'yml', 'xml', 'css',
        'js', 'mjs', 'cjs', 'jsx', 'ts', 'tsx', 'py', 'sql', 'sh', 'bat', 'cmd', 'ps1', 'java', 'c', 'h',
        'cpp', 'hpp', 'cs', 'go', 'rb', 'php', 'rs', 'kt', 'swift', 'srt', 'vtt', 'properties', 'toml'],
    // Старые двоичные форматы Office и форматы Apple/LibreOffice: браузер их не
    // читает, и честнее сразу сказать об этом, чем крутить загрузку.
    legacy: ['doc', 'dot', 'xls', 'xlt', 'ppt', 'pps', 'rtf', 'odt', 'ods', 'odp', 'pages', 'numbers', 'key'],
    archive: ['zip', 'rar', '7z', 'gz', 'tgz', 'tar', 'bz2', 'xz'],
};

const KIND_BY_EXT = new Map();
Object.entries(GROUPS).forEach(([kind, exts]) => exts.forEach((ext) => KIND_BY_EXT.set(ext, kind)));

// Простой текст читается обычным шрифтом, код и журналы — моноширинным:
// в них важны отступы и колонки.
const PROSE_EXT = new Set(['txt', 'text', 'md', 'markdown']);

const MIME_KINDS = [
    [/^application\/pdf$/, 'pdf'],
    [/^image\//, 'image'],
    [/^video\//, 'video'],
    [/^audio\//, 'audio'],
    [/wordprocessingml\.(document|template)/, 'docx'],
    [/spreadsheetml\.(sheet|template)/, 'xlsx'],
    [/presentationml\.(presentation|slideshow|template)/, 'pptx'],
    [/^text\/(csv|tab-separated-values)$/, 'csv'],
    [/^text\/markdown$/, 'markdown'],
    [/^application\/(json|geo\+json)$/, 'json'],
    [/^(text\/html|application\/xhtml\+xml)$/, 'html'],
    [/^(text\/|application\/(xml|javascript|x-sh|x-yaml|yaml|sql)$)/, 'text'],
    [/^application\/(msword|vnd\.ms-excel|vnd\.ms-powerpoint|rtf|vnd\.oasis\.opendocument\.)/, 'legacy'],
    [/^application\/(zip|x-zip-compressed|x-rar-compressed|vnd\.rar|x-7z-compressed|gzip|x-tar)$/, 'archive'],
];

export const fileExtension = (name) => {
    const match = /\.([A-Za-z0-9]{1,10})$/.exec(String(name || '').trim());
    return match ? match[1].toLowerCase() : '';
};

const mimeOf = (file) => String(file?.content_type || file?.mime_type || '')
    .split(';')[0].trim().toLowerCase();

/** { kind, ext, mono } — kind: pdf | image | video | audio | docx | xlsx | pptx |
 *  csv | markdown | json | text | legacy | archive | unknown. */
export function previewKind(file) {
    const ext = fileExtension(file?.file_name || file?.name);
    let kind = KIND_BY_EXT.get(ext) || '';
    if (!kind) {
        const mime = mimeOf(file);
        const hit = MIME_KINDS.find(([pattern]) => pattern.test(mime));
        kind = hit ? hit[1] : 'unknown';
    }
    return { kind, ext, mono: kind === 'text' && !PROSE_EXT.has(ext) && ext !== '' };
}

const startsWith = (bytes, signature, offset = 0) => (
    bytes.length >= offset + signature.length
    && signature.every((value, index) => bytes[offset + index] === value)
);
const ascii = (text) => [...text].map((char) => char.charCodeAt(0));

/** Похоже ли начало файла на двоичные данные: нулевые байты в тексте не встречаются. */
export function looksBinary(bytes) {
    const limit = Math.min(bytes.length, 8192);
    let control = 0;
    for (let index = 0; index < limit; index += 1) {
        const value = bytes[index];
        if (value === 0) return true;
        if (value < 8 || (value > 13 && value < 32 && value !== 27)) control += 1;
    }
    return limit > 0 && control / limit > 0.1;
}

const HTML_START = /^\s*(?:<!--[\s\S]*?-->\s*|<\?[\s\S]*?\?>\s*)*<(?:!doctype\s+html|html)[\s>]/i;

/** Вид файла по первым байтам — для вложений без расширения и без типа. */
export function sniffKind(bytes) {
    if (!bytes || !bytes.length) return 'unknown';
    if (startsWith(bytes, ascii('%PDF-'))) return 'pdf';
    if (startsWith(bytes, [0x89, 0x50, 0x4e, 0x47])) return 'image';
    if (startsWith(bytes, [0xff, 0xd8, 0xff])) return 'image';
    if (startsWith(bytes, ascii('GIF8'))) return 'image';
    if (startsWith(bytes, ascii('RIFF')) && startsWith(bytes, ascii('WEBP'), 8)) return 'image';
    if (startsWith(bytes, [0x50, 0x4b, 0x03, 0x04])) return 'zip';
    if (startsWith(bytes, [0xd0, 0xcf, 0x11, 0xe0])) return 'legacy';
    if (startsWith(bytes, [0x1f, 0x8b]) || startsWith(bytes, ascii('Rar!')) || startsWith(bytes, [0x37, 0x7a, 0xbc, 0xaf])) return 'archive';
    if (startsWith(bytes, ascii('ftyp'), 4)) {
        // Контейнер ISO: тот же заголовок у видео, у звука M4A и у фото HEIC/AVIF.
        const brand = String.fromCharCode(...bytes.slice(8, 12));
        if (/^(heic|heix|mif1|msf1|avif)/.test(brand)) return 'image';
        if (/^M4A/.test(brand)) return 'audio';
        return 'video';
    }
    if (startsWith(bytes, ascii('ID3'))) return 'audio';
    if (looksBinary(bytes)) return 'unknown';
    // Страница без расширения: начало — doctype или <html> (после комментариев и пролога XML).
    const head = String.fromCharCode(...bytes.slice(0, 1024)).replace(/^\uFEFF|^\xEF\xBB\xBF/, '');
    return HTML_START.test(head) ? 'html' : 'text';
}

/** Какой это документ Office, если перед нами zip: по главной части пакета. */
export function officeKindFromEntries(names) {
    const list = new Set(names);
    if (list.has('word/document.xml')) return 'docx';
    if (list.has('xl/workbook.xml')) return 'xlsx';
    if (list.has('ppt/presentation.xml')) return 'pptx';
    return 'archive';
}

// Тип для картинок, видео и звука по расширению: из базы часто приходит
// application/octet-stream, и с ним браузер не рисует SVG и не играет видео.
const MEDIA_TYPES = {
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', jfif: 'image/jpeg', pjpeg: 'image/jpeg',
    gif: 'image/gif', webp: 'image/webp', bmp: 'image/bmp', svg: 'image/svg+xml', avif: 'image/avif',
    ico: 'image/x-icon', heic: 'image/heic', heif: 'image/heif', tif: 'image/tiff', tiff: 'image/tiff',
    mp4: 'video/mp4', m4v: 'video/x-m4v', webm: 'video/webm', mov: 'video/quicktime', ogv: 'video/ogg',
    mp3: 'audio/mpeg', wav: 'audio/wav', ogg: 'audio/ogg', oga: 'audio/ogg', opus: 'audio/ogg',
    m4a: 'audio/mp4', aac: 'audio/aac', flac: 'audio/flac', weba: 'audio/webm',
};

export const mediaTypeOf = (ext) => MEDIA_TYPES[String(ext || '').toLowerCase()] || '';

const TYPE_LABELS = {
    pdf: 'PDF',
    image: 'Изображение',
    video: 'Видео',
    audio: 'Аудио',
    docx: 'Документ Word',
    xlsx: 'Таблица Excel',
    pptx: 'Презентация PowerPoint',
    csv: 'Таблица CSV',
    markdown: 'Markdown',
    json: 'JSON',
    html: 'Страница HTML',
    text: 'Текст',
    archive: 'Архив',
};

export function fileTypeLabel(kind, ext) {
    if (TYPE_LABELS[kind]) return TYPE_LABELS[kind];
    return ext ? `Файл ${ext.toUpperCase()}` : 'Файл';
}

const SIZE_UNITS = ['Б', 'КБ', 'МБ', 'ГБ'];

/** 845 Б · 12 КБ · 1,2 МБ — как в Finder, с запятой. */
export function formatFileSize(bytes) {
    let value = Number(bytes);
    if (!Number.isFinite(value) || value <= 0) return '';
    let unit = 0;
    while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
        value /= 1024;
        unit += 1;
    }
    const rounded = unit === 0 || value >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
    return `${String(rounded).replace('.', ',')} ${SIZE_UNITS[unit]}`;
}

/** 1 страница · 2 страницы · 5 страниц. */
export function plural(count, one, few, many) {
    const n = Math.abs(Number(count)) % 100;
    const last = n % 10;
    if (n > 10 && n < 20) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
}

/** Почему просмотр не показывает файл — словами для человека. */
export function unsupportedReason(kind, ext) {
    const label = ext ? `.${ext}` : 'этого формата';
    if (kind === 'legacy') {
        if (['doc', 'dot'].includes(ext)) return 'Старый формат Word (.doc) браузер не открывает. Скачайте файл или попросите прислать .docx.';
        if (['xls', 'xlt'].includes(ext)) return 'Старый формат Excel (.xls) браузер не открывает. Скачайте файл или попросите прислать .xlsx.';
        if (['ppt', 'pps'].includes(ext)) return 'Старый формат PowerPoint (.ppt) браузер не открывает. Скачайте файл.';
        return `Файлы ${label} браузер не открывает. Скачайте файл, чтобы посмотреть его.`;
    }
    if (kind === 'archive') return 'Архив нельзя посмотреть без распаковки. Скачайте его.';
    return `Предпросмотр файлов ${label} недоступен. Скачайте файл, чтобы открыть его.`;
}
