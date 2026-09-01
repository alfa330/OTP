/* Фото вещи в карточке посылки: что подойдёт, как это уменьшить и где взять адрес.
 *
 * ЗАЧЕМ УМЕНЬШАТЬ В БРАУЗЕРЕ. Посылку заводят у стойки с телефона, и «фото
 * вещи» — это кадр на 3–5 мегабайт с двенадцатимегапиксельной камеры. В
 * карточке он показывается плиткой 96 px и во весь экран телефона, то есть до
 * глаза не доезжает и десятой доли байтов. Уменьшенный до 2048 по длинной
 * стороне кадр в WebP весит 150–250 КБ: десять таких уходят за секунды даже с
 * мобильного интернета в офисе, а исходные тридцать мегабайт — нет.
 *
 * Сервер всё равно переводит в WebP сам (parcels/photos.py поверх
 * wiki/images.py) — здесь это не замена проверке, а экономия трафика ТОГО, кто
 * грузит. Отсюда правило: любая осечка обработки возвращает файл как есть, и
 * загрузка не срывается. Тот же приём и та же причина, что у логотипа парка
 * (src/components/wiki/parkLogo.js).
 *
 * Отдельный модуль, а не часть формы: правила «что подойдёт» обязаны совпадать
 * с серверными (parcels/photos.py: PHOTO_TYPES, MAX_BYTES, MAX_PER_PARCEL), а
 * проверить это тестом (tests/parcel_photo.test.mjs) можно только отдельно от
 * JSX.
 */

/* Сколько фотографий на одну посылку. Решение владельца 01.09.2026: коробку
   снимают с двух сторон, отдельно бирку и отдельно накладную — одного кадра
   мало, но и тридцать в карточке никому не нужны. */
export const PHOTO_MAX_COUNT = 10;

/* Вес ИСХОДНОГО файла, до уменьшения. 20 МБ — с запасом на кадр с телефона в
   максимальном качестве; предел здесь для того, чтобы форма отказала сразу, а
   не после минуты загрузки. */
export const PHOTO_MAX_BYTES = 20 * 1024 * 1024;

/* Форматы, которые уходят на сервер БЕЗ обработки, если её не удалось сделать.
   Всё остальное (HEIC с айфона, TIFF со сканера) обязано сначала успешно
   пережаться в браузере — иначе в бакете окажется файл, который потом не
   покажет ни один браузер.

   SVG нет намеренно, как и у логотипа парка: это исполняемый документ, а не
   картинка. */
export const PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

/* Что кладём в accept у <input type="file">.
 *
 * Список форматов, а НЕ `image/*`, и это не мелочь: на iPhone `image/*`
 * отдаёт снимок в HEIC, который не открывает ни Chrome, ни Pillow на сервере,
 * а с явным списком iOS сам перекодирует кадр в JPEG при выборе. */
export const PHOTO_ACCEPT = PHOTO_TYPES.join(',');

/* Длинная сторона после уменьшения. 2048, а не 1024: на фото часто снимают
   накладную и бирку с номером, и подпись на них должна остаться читаемой при
   увеличении. Больше 2048 смысла нет — на сервере кадр всё равно ужимается до
   2560 (wiki/images.py: MAX_SIDE), а разницы на экране телефона не видно. */
export const PHOTO_SIDE = 2048;

/* Качество WebP с потерями. 0.85 — фотография, а не скриншот: на снимке
   коробки разницы с исходником не видно, а вес падает втрое. */
export const PHOTO_QUALITY = 0.85;

/* Сколько ждём кодировщик, прежде чем считать, что он не ответит (см.
   shrinkPhoto). Восемь секунд — заведомо больше, чем нужно любому телефону на
   кадр в 2048 пикселей, и заведомо меньше, чем человек готов смотреть на
   зависшую форму. */
export const TOBLOB_TIMEOUT_MS = 8000;


/* Что не так с файлом. Строкой, а не булевым: отказ без причины выглядит
   поломкой формы. Порядок проверок — от самой понятной человеку. */
export const photoIssue = (file) => {
    if (!file) return 'Файл не выбран';
    const type = String(file.type || '').toLowerCase();
    // Пустой тип бывает у файла, перетащенного из некоторых программ: судим
    // тогда по расширению, а не отказываем — перетаскивание ради этого и есть.
    const looksImage = type.startsWith('image/')
        || (!type && /\.(jpe?g|png|webp|gif|heic|heif|bmp|tiff?)$/i.test(String(file.name || '')));
    if (!looksImage) return 'Подойдёт только фотография';
    if (type === 'image/svg+xml') return 'SVG — это не фотография';
    if (file.size > PHOTO_MAX_BYTES) {
        return `Файл больше ${Math.round(PHOTO_MAX_BYTES / (1024 * 1024))} МБ`;
    }
    if (!file.size) return 'Файл пустой';
    return null;
};


/* Сколько ещё поместится. Отдельно от photoIssue, потому что причина другая и
   показывается она один раз на всю пачку, а не у каждого файла. */
export const countIssue = (current, adding) => {
    const free = PHOTO_MAX_COUNT - Math.max(0, current || 0);
    if (free <= 0) return `Больше ${PHOTO_MAX_COUNT} фотографий к одной посылке не прикрепить`;
    // «Поместится ещё N …», а не «осталось место для N …»: после «для» нужен
    // родительный падеж («для 2 фотографиЙ»), и одному счётчику пришлось бы
    // знать два склонения. Формулировка выбрана так, чтобы хватило одного.
    if ((adding || 0) > free) {
        return `Поместится ещё ${free} ${pluralPhotos(free)} — остальные не добавлены`;
    }
    return null;
};


/* Размер после уменьшения: длинная сторона не больше PHOTO_SIDE, пропорции
   сохраняются, кадр меньше предела не растягивается. */
export const fitPhoto = (width, height) => {
    const side = Math.max(width || 0, height || 0);
    if (!side) return { width: 0, height: 0 };
    const scale = Math.min(1, PHOTO_SIDE / side);
    return {
        width: Math.max(1, Math.round(width * scale)),
        height: Math.max(1, Math.round(height * scale)),
    };
};


/* Имя файла с расширением .webp — как webp_name на сервере (wiki/images.py).
   Расширение меняется вместе с содержимым: «коробка.jpg», внутри которого
   лежит WebP, — ровно та мелочь, на которой через полгода теряют полчаса. */
export const webpName = (original) => {
    const base = String(original || '').replace(/^.*[\\/]/, '').replace(/\.[^.]+$/, '').trim();
    return `${base.replace(/^\.+|\.+$/g, '') || 'photo'}.webp`;
};


/* Файл из проводника, камеры или буфера → то, что уйдёт на сервер.
 *
 * Возвращает { ok: true, blob, name, width, height, converted } либо
 * { ok: false, issue } — с причиной, которую можно показать человеку.
 *
 * Осечка обработки САМА ПО СЕБЕ не отказ: JPEG и PNG уходят исходником, потому
 * что сервер их и так примет и переведёт. Отказ остаётся только там, где иначе
 * в бакет лёг бы файл, который потом никто не откроет: HEIC с айфона в
 * браузере, который его не разобрал, и Pillow без плагина HEIF на сервере.
 */
export const preparePhoto = async (file) => {
    const issue = photoIssue(file);
    if (issue) return { ok: false, issue, file };

    const type = String(file.type || '').toLowerCase();
    const shrunk = await shrinkPhoto(file);
    if (shrunk) return { ok: true, file, ...shrunk };

    if (!PHOTO_TYPES.includes(type)) {
        return {
            ok: false,
            file,
            issue: 'Этот формат браузер не открыл — сохраните фото как JPEG',
        };
    }
    return {
        ok: true, file, blob: file, name: file.name || 'photo', converted: false,
        width: null, height: null,
    };
};


/* Уменьшение и перевод в WebP. null — не получилось (см. preparePhoto).
 *
 * imageOrientation: 'from-image' — не украшение. Снимок с телефона хранит
 * поворот отдельным полем EXIF, а canvas рисует ПИКСЕЛИ как есть: без этого
 * ключа половина фотографий уезжала бы в карточку боком. Логотип парка этой
 * беды не знает (его выбирают из готовых файлов), поэтому в parkLogo.js ключа
 * нет — копировать оттуда вызов дословно было бы ошибкой.
 */
export const shrinkPhoto = async (file) => {
    if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') return null;

    let bitmap = null;
    try {
        try {
            bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
        } catch {
            // Браузер без поддержки настроек createImageBitmap (старый Safari)
            // — пробуем без них: кадр боком лучше, чем отказ.
            bitmap = await createImageBitmap(file);
        }
        const { width, height } = fitPhoto(bitmap.width, bitmap.height);
        if (!width || !height) return null;

        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        canvas.getContext('2d').drawImage(bitmap, 0, 0, width, height);

        // Сторож обязателен. `toBlob` отдаёт результат колбэком, и на части
        // Android WebView этот колбэк не приходит НИКОГДА: без таймера промис
        // не разрешился бы, а форма осталась бы в «обработке» навсегда — то
        // есть человек у стойки не смог бы сохранить посылку вообще.
        const blob = await new Promise((resolve) => {
            const guard = setTimeout(() => resolve(null), TOBLOB_TIMEOUT_MS);
            canvas.toBlob((result) => {
                clearTimeout(guard);
                resolve(result);
            }, 'image/webp', PHOTO_QUALITY);
        });
        // Браузер без WebP отдаёт null или молча подсовывает PNG.
        if (!blob || blob.type !== 'image/webp') return null;

        // Пережали, а стало тяжелее — значит не пережимали, а испортили. То же
        // правило, что на сервере (wiki/images.py): у уже сжатого кадра в
        // габаритах второй проход кодека даёт только потери.
        if (blob.size >= file.size && width === bitmap.width && height === bitmap.height) {
            return PHOTO_TYPES.includes(String(file.type || '').toLowerCase())
                ? { blob: file, name: file.name || 'photo', width, height, converted: false }
                : { blob, name: webpName(file.name), width, height, converted: true };
        }
        return { blob, name: webpName(file.name), width, height, converted: true };
    } catch {
        return null;
    } finally {
        bitmap?.close?.();
    }
};


/* Порядок показа: как поставил человек, а при равенстве — как загружали.
   Сортируем на показе, а не полагаемся на порядок ответа: список приезжает и
   из карточки, и из ответа на загрузку, и совпасть они обязаны. */
export const sortPhotos = (photos) => (
    [...(photos || [])].sort((left, right) => (
        (left?.sort_order ?? 0) - (right?.sort_order ?? 0)
        || String(left?.created_at || '').localeCompare(String(right?.created_at || ''))
        || String(left?.id || '').localeCompare(String(right?.id || ''))
    ))
);


/* «фотография / фотографии / фотографий» — счётчик стоит и в форме, и в
   отказе, и в подписи под плиткой. */
export const pluralPhotos = (count) => {
    const value = Math.abs(Number(count) || 0);
    const tail = value % 100;
    if (tail >= 11 && tail <= 14) return 'фотографий';
    switch (value % 10) {
        case 1: return 'фотография';
        case 2:
        case 3:
        case 4: return 'фотографии';
        default: return 'фотографий';
    }
};
