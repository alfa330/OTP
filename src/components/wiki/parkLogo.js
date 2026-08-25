/* Логотип («аватарка») таксопарка: проверка файла, уменьшение и перевод в WebP.
 *
 * Уменьшаем и пересобираем в браузере, а не на сервере. В рельсе витрины
 * плитка 38×38, в карточке справочника 40×40, на странице парка 56×56 — а
 * выбирают для них то, что нашлось под рукой: скриншот или фотографию на пару
 * мегабайт. Пятнадцать таких логотипов на витрине — это пятнадцать лишних
 * мегабайт при каждом заходе на главную; после обработки от них остаются
 * десятки килобайт.
 *
 * В хранилище логотип уходит ТОЛЬКО в WebP (решение владельца 25.08.2026):
 * формат исходника роли не играет, а PNG со скриншота весит в разы больше
 * того же кадра в WebP. Исключение одно и не наше — браузер, который WebP не
 * умеет вовсе: там уходит исходный файл, иначе загрузка просто не состоялась
 * бы. Поэтому сервер принимает и PNG с JPEG (wiki/routes_parks.py).
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

/* Файл → {blob, name, ratio} для отправки.
 *
 * ratio (ширина/высота исходника) уезжает в ракурс: по нему плитка знает
 * геометрию картинки ещё до того, как та загрузится.
 *
 * Любая осечка (браузер без createImageBitmap или без WebP, картинка, которую
 * он не разобрал) — это исходный файл как есть: обработка здесь экономия, а не
 * условие загрузки, и падать из-за неё форма не должна. Сервер всё равно
 * проверит тип и размер сам.
 */
export const shrinkLogo = async (file) => {
    const asIs = { blob: file, name: file?.name || 'logo', ratio: null };
    if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') return asIs;

    try {
        const bitmap = await createImageBitmap(file);
        const ratio = bitmap.width && bitmap.height ? bitmap.width / bitmap.height : null;
        const { width, height } = fitLogo(bitmap.width, bitmap.height);

        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        canvas.getContext('2d').drawImage(bitmap, 0, 0, width, height);
        bitmap.close?.();

        // Пересобираем всегда, даже если картинка уже маленькая: одинаковый
        // формат в хранилище дороже сэкономленной доли секунды. WebP держит и
        // прозрачность, которая у логотипов обычна.
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/webp', 0.9));
        // Браузер без WebP отдаёт null (или подсовывает PNG) — тогда исходник.
        if (!blob || blob.type !== 'image/webp') return { ...asIs, ratio };
        return { blob, name: `${String(file.name || 'logo').replace(/\.[^.]+$/, '')}.webp`, ratio };
    } catch {
        return asIs;
    }
};


/* ─── Ракурс: какая часть картинки видна в квадратной плитке ──────────────
 *
 * Логотип показывается плиткой, а вывеска парка обычно широкая — браузер по
 * object-cover брал середину, то есть кусок фона между словами. Ракурс это
 * четыре числа рядом с картинкой:
 *
 *   zoom  — во сколько раз крупнее «вписанной» (1 — целиком по короткой стороне);
 *   x, y  — какая доля лишнего срезана слева и сверху (0…1);
 *   ratio — соотношение сторон исходника.
 *
 * Файл при этом не режется: обрезать пиксели значило бы решить один раз
 * навсегда — чтобы отступить обратно, картинку пришлось бы загружать заново.
 *
 * Ratio хранится вместе с остальными, а не измеряется при показе: без него
 * плитка не знает геометрии, пока картинка не загрузилась, и логотип на долю
 * секунды прыгал бы из середины в выбранное место при каждом заходе.
 */

export const ZOOM_MIN = 1;
export const ZOOM_MAX = 4;

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);

export const makeFrame = (ratio = 1, patch = {}) => ({
    zoom: clamp(isNumber(patch.zoom) ? patch.zoom : 1, ZOOM_MIN, ZOOM_MAX),
    x: clamp(isNumber(patch.x) ? patch.x : 0.5, 0, 1),
    y: clamp(isNumber(patch.y) ? patch.y : 0.5, 0, 1),
    ratio: clamp(isNumber(ratio) && ratio > 0 ? ratio : 1, 0.05, 20),
});

/* Чужое значение (из ответа сервера, из старой записи) → рабочий ракурс или
   null. Null значит «как раньше»: середина без увеличения, обычный object-cover
   — им же плитка и рисуется, лишнего слоя стилей на такой логотип не вешаем. */
export const normalizeFrame = (frame) => {
    if (!frame || typeof frame !== 'object') return null;
    const next = makeFrame(Number(frame.ratio), {
        zoom: Number(frame.zoom), x: Number(frame.x), y: Number(frame.y),
    });
    return next;
};

/* Геометрия картинки внутри плитки, в процентах самой плитки.
 *
 * Проценты, а не пиксели, потому что плитка бывает 38 px в рельсе и 176 px в
 * форме — а ракурс у них один и тот же, и пересчитывать его на каждый размер
 * значило бы хранить размер вместе с ракурсом. */
export const frameLayout = (frame) => {
    const { zoom, x, y, ratio } = makeFrame(frame?.ratio, frame || {});
    const width = 100 * zoom * Math.max(1, ratio);
    const height = 100 * zoom * Math.max(1, 1 / ratio);
    return {
        width,
        height,
        left: -(width - 100) * x,
        top: -(height - 100) * y,
    };
};

/* Стили для <img>. null — ракурса нет, рисуем обычным object-cover. */
export const frameStyle = (frame) => {
    const normalized = normalizeFrame(frame);
    if (!normalized) return null;
    const box = frameLayout(normalized);
    return {
        position: 'absolute',
        width: `${box.width}%`,
        height: `${box.height}%`,
        left: `${box.left}%`,
        top: `${box.top}%`,
        // Tailwind ставит картинкам max-width: 100% — без снятия ширина в
        // процентах молча обрезается по плитке, и увеличение не работает вовсе.
        maxWidth: 'none',
        objectFit: 'fill',
    };
};

/* Сдвиг картинки пальцем: dx, dy — в пикселях плитки размером box.
 *
 * Тянут КАРТИНКУ, а не рамку: вправо — значит показать то, что было левее,
 * поэтому доля срезанного слева уменьшается. Границы 0…1 держат кадр внутри
 * картинки сами — пустого поля с краю не получится. */
export const panFrame = (frame, dx, dy, box) => {
    const current = makeFrame(frame?.ratio, frame || {});
    if (!box) return current;
    const layout = frameLayout(current);
    const overflowX = box * (layout.width - 100) / 100;
    const overflowY = box * (layout.height - 100) / 100;
    return {
        ...current,
        x: overflowX > 0.5 ? clamp(current.x - dx / overflowX, 0, 1) : current.x,
        y: overflowY > 0.5 ? clamp(current.y - dy / overflowY, 0, 1) : current.y,
    };
};

export const zoomFrame = (frame, zoom) => makeFrame(frame?.ratio, { ...frame, zoom });

/* Ракурс «по умолчанию» отличается от отсутствия ракурса только тем, что о нём
   известно соотношение сторон, — сервер такой не хранит (routes_parks:
   _logo_frame). Форма же его держит: без ratio нечего показывать в окошке. */
export const isDefaultFrame = (frame) => {
    const normalized = normalizeFrame(frame);
    return !normalized
        || (normalized.zoom === 1 && normalized.x === 0.5 && normalized.y === 0.5);
};
