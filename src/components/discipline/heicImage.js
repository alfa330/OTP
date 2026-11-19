/* HEIC в разделе «Дисциплина»: фото объяснительной с iPhone.
 *
 * ЗАЧЕМ. ТЗ (п. 8.1, 13) разрешает HEIC, и iPhone, выбирая снимок в поле
 * файла, где HEIC разрешён, отдаёт его как есть. Показать такой файл умеет
 * только Safari: в Chrome и Edge «предпросмотр без скачивания» (п. 13)
 * превращался в скачивание файла, который на Windows ещё и нечем открыть.
 *
 * ЧТО ДЕЛАЕМ. Перед отправкой HEIC пережимается в JPEG прямо в браузере — там,
 * где браузер умеет его прочесть (Safari на iPhone и Mac — то есть почти всегда,
 * когда HEIC вообще появляется). Не умеет — файл уходит как есть: сервер HEIC
 * принимает, ничего не теряется. Тот же приём, что у фото посылок
 * (src/components/parcels/parcelPhoto.js), но в JPEG, а не в WebP: WebP раздел
 * не принимает (catalog.FILE_KINDS), а JPEG открывается везде.
 *
 * Уже загруженный HEIC окно просмотра пробует показать тем же переводом
 * (heicToJpeg) и скачивает, только если браузер не прочёл его.
 *
 * Без DOM (в node-тестах) работают чистые части: isHeic, jpegName, fitSide.
 */

/* Длинная сторона после перевода. Снимок 12 Мп с iPhone (4032 px) проходит
   как есть — рукописный текст должен читаться при увеличении; 48 Мп ужимается,
   иначе JPEG вышел бы за предел 10 МБ объяснительной. */
export const HEIC_MAX_SIDE = 4096;

/* Качество JPEG: документ, а не пейзаж, — строчки почерка не должны мылиться. */
export const HEIC_JPEG_QUALITY = 0.9;

/* Сколько ждём кодировщик: на части Android WebView колбэк toBlob не приходит
   никогда (см. parcelPhoto.js) — без сторожа загрузка зависла бы. */
export const HEIC_TIMEOUT_MS = 8000;

const HEIC_TYPES = ['image/heic', 'image/heif', 'image/heic-sequence', 'image/heif-sequence'];

/* HEIC ли это: по типу, а без типа (перетащили из программы, Windows не знает
   HEIC) — по расширению. */
export const isHeic = (file) => {
    const type = String(file?.type || file?.content_type || '').toLowerCase();
    if (HEIC_TYPES.includes(type)) return true;
    if (type && type !== 'application/octet-stream') return false;
    return /\.(heic|heif)$/i.test(String(file?.name || ''));
};

/* «Объяснительная.HEIC» → «Объяснительная.jpg»: расширение меняется вместе с
   содержимым, иначе через месяц не понять, что лежит внутри. */
export const jpegName = (name) => {
    const base = String(name || '').replace(/^.*[\\/]/, '').replace(/\.(heic|heif)$/i, '').trim();
    return `${base || 'photo'}.jpg`;
};

/* Размер после перевода: длинная сторона не больше HEIC_MAX_SIDE, пропорции те же. */
export const fitSide = (width, height, maxSide = HEIC_MAX_SIDE) => {
    const side = Math.max(width || 0, height || 0);
    if (!side) return { width: 0, height: 0 };
    const scale = Math.min(1, maxSide / side);
    return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
};

/* HEIC (File или Blob) → JPEG Blob, либо null, если браузер его не прочёл.
   imageOrientation 'from-image' — поворот снимка лежит в EXIF отдельно от
   пикселей; без него половина фото легла бы боком. */
export const heicToJpeg = async (source) => {
    if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') return null;
    let bitmap = null;
    try {
        try {
            bitmap = await createImageBitmap(source, { imageOrientation: 'from-image' });
        } catch {
            bitmap = await createImageBitmap(source);
        }
        const { width, height } = fitSide(bitmap.width, bitmap.height);
        if (!width || !height) return null;
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext('2d');
        // JPEG без прозрачности: прозрачное легло бы чёрным.
        context.fillStyle = '#ffffff';
        context.fillRect(0, 0, width, height);
        context.drawImage(bitmap, 0, 0, width, height);
        const blob = await new Promise((resolve) => {
            const guard = setTimeout(() => resolve(null), HEIC_TIMEOUT_MS);
            canvas.toBlob((result) => { clearTimeout(guard); resolve(result); }, 'image/jpeg', HEIC_JPEG_QUALITY);
        });
        return blob && blob.type === 'image/jpeg' ? blob : null;
    } catch {
        return null;
    } finally {
        bitmap?.close?.();
    }
};

/* Выбранные файлы → что уйдёт на сервер: HEIC — JPEG-ом, если вышло, остальное
   как есть. Порядок файлов сохраняется. */
export const prepareUploads = async (files) => Promise.all(Array.from(files || []).map(async (file) => {
    if (!isHeic(file)) return file;
    const jpeg = await heicToJpeg(file);
    if (!jpeg) return file;
    try {
        return new File([jpeg], jpegName(file.name), { type: 'image/jpeg', lastModified: file.lastModified || Date.now() });
    } catch {
        // Старый Safari без конструктора File: Blob с именем сервер примет так же.
        jpeg.name = jpegName(file.name);
        return jpeg;
    }
}));
