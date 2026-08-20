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
    { label: 'Документ', icon: FileText, tone: 'text-blue-600 bg-blue-50',
      exts: ['doc', 'docx', 'rtf', 'odt', 'txt', 'md'] },
    // PDF отдельной группой, хотя значок тот же: красный за ним закрепился
    // настолько, что в списке из пяти файлов его находят по цвету, не читая.
    { label: 'PDF', icon: FileText, tone: 'text-red-600 bg-red-50', exts: ['pdf'] },
    { label: 'Таблица', icon: FileSpreadsheet, tone: 'text-emerald-600 bg-emerald-50',
      exts: ['xls', 'xlsx', 'xlsm', 'csv', 'ods'] },
    { label: 'Презентация', icon: Presentation, tone: 'text-amber-600 bg-amber-50',
      exts: ['ppt', 'pptx', 'odp', 'key'] },
    { label: 'Картинка', icon: FileImage, tone: 'text-violet-600 bg-violet-50',
      exts: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'heic'] },
    { label: 'Архив', icon: FileArchive, tone: 'text-slate-600 bg-slate-100',
      exts: ['zip', 'rar', '7z', 'tar', 'gz'] },
];

const OTHER = { label: 'Файл', icon: File, tone: 'text-slate-600 bg-slate-100' };

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
