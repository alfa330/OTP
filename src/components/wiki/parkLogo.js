/* Логотип («аватарка») таксопарка: проверка файла и уменьшение до аватарки.
 *
 * Уменьшаем в браузере, а не на сервере. В рельсе витрины плитка 38×38, в
 * карточке справочника 40×40, на странице парка 56×56 — а выбирают для них то,
 * что нашлось под рукой: скриншот или фотографию на пару мегабайт. Пятнадцать
 * таких логотипов на витрине — это пятнадцать лишних мегабайт при каждом
 * заходе на главную; после уменьшения от них остаются десятки килобайт.
 *
 * Отдельный модуль, а не часть формы: правила «что подойдёт» одинаковы у
 * формы и у сервера (wiki/routes_parks.py: _LOGO_TYPES, _LOGO_MAX_BYTES),
 * и проверить их тестом (tests/wiki_park_logo.test.mjs) можно только отдельно
 * от JSX.
 */

/* Пять мегабайт, как на сервере. Расхождение здесь читалось бы как «форма
   приняла, а сервер отказал» — то есть как поломка, а не как правило. */
export const LOGO_MAX_BYTES = 5 * 1024 * 1024;

/* SVG нет намеренно, и тоже как на сервере: это исполняемый документ, а не
   картинка. */
export const LOGO_TYPES = ['image/png', 'image/jpeg', 'image/webp'];

/* Сторона аватарки после уменьшения. 512, а не 128: тот же файл показывается и
   плиткой 38 px, и на экране с двойной плотностью, и когда-нибудь крупнее — а
   пересжать уже уменьшенное нельзя. */
export const LOGO_SIDE = 512;

/* Что не так с файлом. Строкой, а не булевым: отказ без причины выглядит
   поломкой формы (то же правило, что у parkDraftIssue). */
export const logoIssue = (file) => {
    if (!file) return 'Файл не выбран';
    if (!LOGO_TYPES.includes(file.type)) return 'Подойдёт картинка PNG, JPEG или WebP';
    if (file.size > LOGO_MAX_BYTES) {
        return `Файл больше ${Math.round(LOGO_MAX_BYTES / (1024 * 1024))} МБ`;
    }
    return null;
};

/* Размер после уменьшения: длинная сторона не больше LOGO_SIDE, пропорции
   сохраняются, картинка меньше предела не растягивается. */
export const fitLogo = (width, height) => {
    const side = Math.max(width || 0, height || 0);
    if (!side) return { width: 0, height: 0 };
    const scale = Math.min(1, LOGO_SIDE / side);
    return {
        width: Math.max(1, Math.round(width * scale)),
        height: Math.max(1, Math.round(height * scale)),
    };
};

/* Файл → {blob, name} для отправки.
 *
 * Любая осечка (браузер без createImageBitmap, картинка, которую он не
 * разобрал) — это исходный файл как есть: уменьшение здесь ускорение, а не
 * условие загрузки, и падать из-за него форма не должна. Сервер всё равно
 * проверит тип и размер сам.
 */
export const shrinkLogo = async (file) => {
    const asIs = { blob: file, name: file?.name || 'logo' };
    if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') return asIs;

    try {
        const bitmap = await createImageBitmap(file);
        const { width, height } = fitLogo(bitmap.width, bitmap.height);
        // Картинка и так маленькая — не трогаем: пересжатие только испортило
        // бы уже готовый логотип.
        if (width === bitmap.width && height === bitmap.height && file.size <= 300 * 1024) {
            bitmap.close?.();
            return asIs;
        }

        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        canvas.getContext('2d').drawImage(bitmap, 0, 0, width, height);
        bitmap.close?.();

        // WebP держит прозрачность (у логотипов она обычная) и весит меньше
        // PNG. Не вышло — остаёмся на исходнике, а не на PNG наугад.
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/webp', 0.92));
        if (!blob || blob.size >= file.size) return asIs;
        return { blob, name: `${String(file.name || 'logo').replace(/\.[^.]+$/, '')}.webp` };
    } catch {
        return asIs;
    }
};
