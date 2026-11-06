# -*- coding: utf-8 -*-
"""Фото для «Доски почёта»: проверка и пересжатие перед загрузкой в хранилище.

Фон вырезает окно тренера в браузере (src/components/events/cutout/) и
присылает готовый снимок — PNG или WEBP с прозрачностью. Сервер файлу не
верит на слово: открывает его Pillow, отсекает не-картинки и «бомбы»
размером в сотни мегапикселей, поворачивает по EXIF и пересобирает в WEBP.
Пересборка заодно снимает метаданные: снимок с телефона несёт в EXIF модель
телефона, время и иногда координаты места съёмки.

Здесь нет ни Flask, ни базы, ни хранилища — только байты на входе и выходе.
"""

from io import BytesIO

from PIL import Image, ImageOps

# Файл из окна — уже уменьшенный снимок (до 1600 px по длинной стороне), это
# сотни килобайт. Потолок с запасом на случай, когда браузер вырезать фон не
# смог и шлёт фото с телефона почти как есть.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
# Длинная сторона снимка в хранилище: карточка на доске — до ~520 px шириной,
# на экране с ретиной это ~1000 px; 1600 — с запасом на крупную главную карточку.
MAX_SIDE = 1600
MIN_SIDE = 64
# Больше — не снимок из окна (оно присылает до 1600 px), а попытка занять память
# распаковкой: RGBA 8000 × 6000 — это почти полгигабайта на один запрос.
MAX_PIXELS = 4096 * 4096
FORMATS = ('JPEG', 'PNG', 'WEBP')
CONTENT_TYPE = 'image/webp'


class PhotoError(ValueError):
    """Фото не подходит — 400 с этим текстом."""


def _has_alpha(image):
    return image.mode in ('RGBA', 'LA', 'PA') or (image.mode == 'P' and 'transparency' in image.info)


def prepare_photo(data, *, cutout):
    """bytes загруженного файла -> (bytes WEBP, ширина, высота, cutout).

    cutout — окно вырезало фон: снимок с прозрачностью. Прозрачности в файле
    нет — значит, это фото как есть, и cutout становится False: доска рисует
    его прямоугольником, а не фигурой без фона.
    """
    if not data:
        raise PhotoError('Файл пустой — выберите фото ещё раз')
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoError(f'Фото больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ — выберите поменьше')
    try:
        image = Image.open(BytesIO(data))
        image_format = image.format
        width, height = image.size
    except Exception:  # noqa: BLE001 — любая ошибка разбора: это не картинка
        raise PhotoError('Не удалось прочитать фото — подойдёт JPG, PNG или WEBP') from None
    if image_format not in FORMATS:
        raise PhotoError('Не удалось прочитать фото — подойдёт JPG, PNG или WEBP')
    if width * height > MAX_PIXELS:
        raise PhotoError('Фото слишком большое — уменьшите его и загрузите снова')
    if min(width, height) < MIN_SIDE:
        raise PhotoError('Фото слишком маленькое — нужно хотя бы 64 px по каждой стороне')
    try:
        if image_format == 'JPEG':
            # JPEG распаковывается сразу уменьшенным (кратно 2), а не целиком.
            image.draft('RGB', (MAX_SIDE * 2, MAX_SIDE * 2))
        image = ImageOps.exif_transpose(image)
        transparent = _has_alpha(image)
        keep_alpha = bool(cutout) and transparent
        if keep_alpha:
            image = image.convert('RGBA')
        elif transparent:
            # Фото «с фоном», но в файле есть прозрачность (фон вырезали в другой
            # программе): прозрачное — белым, а не чёрным, как дал бы convert('RGB').
            image = Image.alpha_composite(Image.new('RGBA', image.size, (255, 255, 255, 255)),
                                          image.convert('RGBA')).convert('RGB')
        else:
            image = image.convert('RGB')
    except Exception:  # noqa: BLE001 — битый файл всплывает при распаковке
        raise PhotoError('Не удалось прочитать фото — файл повреждён') from None
    if keep_alpha:
        box = image.getchannel('A').point(lambda value: 255 if value > 24 else 0).getbbox()
        if box is None:
            raise PhotoError('После вырезания фона на фото никого не осталось — выберите другое фото')
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = BytesIO()
    # method=4 — разумный баланс времени и размера; у RGBA прозрачность
    # сохраняется вместе с цветом.
    image.save(out, 'WEBP', quality=90, method=4)
    return out.getvalue(), image.width, image.height, keep_alpha
