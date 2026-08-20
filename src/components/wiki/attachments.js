/* Приложения к статье: как одна и та же строка файла выглядит у редактора и у
 * читателя.
 *
 * Вынесено из компонентов, потому что мест показа два (панель в редакторе и
 * блок под текстом статьи), а правило одно: человек должен понять, ЧТО он
 * скачивает, до того как нажал. Отсюда и вид иконки, и подпись формата рядом с
 * размером — «PDF · 1,2 МБ» отвечает на вопрос «открою я это на телефоне?»
 * лучше, чем имя файла вида «scan_0012.pdf».
 *
 * Тип определяем по РАСШИРЕНИЮ, а не по content_type. MIME приходит от
 * браузера, который его угадывает: docx нередко приезжает как
 * application/octet-stream, а csv — как text/plain. Расширение в этом смысле
 * честнее: его написал человек, назвавший файл.
 */

import {
    File, FileArchive, FileImage, FileSpreadsheet, FileText, Presentation,
} from 'lucide-react';

export const fileExtension = (name) => {
    const match = /\.([A-Za-z0-9]{1,8})$/.exec(String(name || '').trim());
    return match ? match[1].toLowerCase() : '';
};

/* Группы намеренно широкие: читателю важно «таблица / текст / картинка», а не
   то, чем именно её открыть. Пять групп и «прочее» покрывают весь корпус. */
const KINDS = [
    { slug: 'doc', label: 'Документ', icon: FileText, tone: 'text-blue-600 bg-blue-50',
      exts: ['doc', 'docx', 'rtf', 'odt', 'txt', 'md'] },
    // PDF отдельной группой, хотя значок тот же: красный за ним закрепился
    // настолько, что в списке из пяти файлов его находят по цвету, не читая.
    { slug: 'pdf', label: 'PDF', icon: FileText, tone: 'text-red-600 bg-red-50', exts: ['pdf'] },
    { slug: 'sheet', label: 'Таблица', icon: FileSpreadsheet, tone: 'text-emerald-600 bg-emerald-50',
      exts: ['xls', 'xlsx', 'xlsm', 'csv', 'ods'] },
    { slug: 'slides', label: 'Презентация', icon: Presentation, tone: 'text-amber-600 bg-amber-50',
      exts: ['ppt', 'pptx', 'odp', 'key'] },
    { slug: 'image', label: 'Картинка', icon: FileImage, tone: 'text-violet-600 bg-violet-50',
      exts: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'heic'] },
    { slug: 'archive', label: 'Архив', icon: FileArchive, tone: 'text-slate-600 bg-slate-100',
      exts: ['zip', 'rar', '7z', 'tar', 'gz'] },
];

const OTHER = { slug: 'file', label: 'Файл', icon: File, tone: 'text-slate-600 bg-slate-100' };

export const attachmentKind = (name) => {
    const ext = fileExtension(name);
    const kind = KINDS.find((k) => k.exts.includes(ext));
    // Подпись — само расширение, когда оно известно: «PDF» точнее, чем
    // «Документ», а «Файл» остаётся только у безымянных форматов.
    return { ...(kind || OTHER), label: ext ? ext.toUpperCase() : (kind || OTHER).label };
};

/* Размер по-русски: килобайты дробью не пишем (в «238,4 КБ» дробь ничего не
   решает), мегабайты пишем — разница между 1,2 и 24 МБ это разница между
   «скачаю сейчас» и «подожду вайфая». */
export const formatBytes = (size) => {
    const value = Number(size) || 0;
    if (value <= 0) return '';
    if (value < 1024) return `${value} Б`;
    if (value < 1024 * 1024) return `${Math.round(value / 1024)} КБ`;
    return `${(value / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ`;
};

/* Строка под именем файла: «PDF · 1,2 МБ». Разделитель добавляется только
   между непустыми частями — у файла нулевого размера точки-сироты не будет. */
export const attachmentMeta = (attachment) => [
    attachmentKind(attachment?.name).label,
    formatBytes(attachment?.size),
].filter(Boolean).join(' · ');

/* ─────────────────────────────────────────────────────────────────────────
   Файл ВНУТРИ текста статьи.

   Второй способ приложить документ, и он не заменяет список под статьёй, а
   отвечает на другой вопрос. Список — «что приложено к статье вообще»;
   ссылка в тексте — «скачайте бланк ИМЕННО ЗДЕСЬ, на этом шаге инструкции».
   Человеку, который читает пункт «заполните заявление», незачем искать файл
   в конце документа.

   Это обычная ссылка <a class="wiki-file …">, а не собственный узел редактора,
   и так сделано намеренно:

     * тело статьи санитизируется на сервере белым списком (wiki/sanitize.py),
       где у <a> разрешены href, target и class — и НЕ разрешены data-*.
       Карточка на data-атрибутах молча теряла бы вид после первого сохранения;
     * ссылка переживает любой перенос текста — копирование в другую статью,
       импорт, правку через ИИ. Кастомный узел пришлось бы учить каждому из
       этих путей;
     * привязка файла к статье уже умеет находить такие ссылки: она ищет
       /api/wiki/file/<uuid> в тексте (wiki/edit.py: _FILE_REF), и адрес с
       ?download=1 попадает под неё без изменений.

   Всё оформление — на CSS по классу (wiki-theme.css), поэтому в редакторе и
   в готовой статье карточка выглядит одинаково: класс .wiki-prose стоит на
   обоих контейнерах.
   ───────────────────────────────────────────────────────────────────────── */

const escapeHtml = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

export const fileLinkHtml = (attachment) => {
    const name = String(attachment?.name || 'Файл');
    const kind = attachmentKind(name);
    const size = formatBytes(attachment?.size);
    // Размер — частью ТЕКСТА ссылки, а не отдельным элементом: вложенный <span>
    // внутри ссылки редактор при разборе схлопнул бы в простой текст, а
    // псевдоэлемент CSS не переживает копирование статьи в письмо или чат.
    const label = size ? `${name} · ${size}` : name;
    // Адрес всегда «скачать»: файл в тексте прикладывают, чтобы его забрали.
    // Открыть его читатель всё равно сможет — браузер сам покажет то, что умеет.
    const href = attachment?.download_url || `${attachment?.url || ''}?download=1`;
    return `<a class="wiki-file wiki-file--${kind.slug}" href="${escapeHtml(href)}"`
        + ` target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`;
};
