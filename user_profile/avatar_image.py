# -*- coding: utf-8 -*-
"""Фото профиля в бакет уходит только в WebP — это решает сервер, а не браузер.

Кружок фото вырезает и кодирует окно (AccountAvatarModal, UserEditModal):
canvas.toBlob('image/webp'). Safari WebP с холста не кодирует и молча отдаёт
PNG, а окно всё равно называет файл «.webp» и подписывает image/webp. Так на
проде и лежал PNG под именем .webp (фото СЗоВ от 29.10.2026). Фото профиля
подставляет «Доска почёта» (#318), когда тренер не загрузил своё, — а владелец
поставил правило: загруженные фото хранятся в WebP.

Поэтому подписи файла здесь не верят: байты открывает Pillow и пересобирает в
WebP всегда, даже настоящий WebP. Общий конвертер вики (wiki/images.to_webp)
не подходит: «уже WebP» он узнаёт по ПОДПИСИ и такой файл пропускает как есть —
то есть ровно этот PNG. Пересборка заодно отсекает не-картинки и «бомбы» на
сотни мегапикселей и снимает метаданные (EXIF с телефона: модель, время, место).

Здесь нет ни Flask, ни базы, ни хранилища — только байты на входе и выходе.
Этой же функцией переведены старые фото на проде, чтобы правило было одно.
"""

from io import BytesIO

CONTENT_TYPE = 'image/webp'
# Окно присылает кружок 128 × 128 px. Крупнее — только не из окна; потолок
# держит фото профиля лёгким: шире ~520 px его не показывает даже доска почёта.
MAX_SIDE = 1024
# Больше — не фото профиля, а попытка занять память распаковкой: RGBA
# 8000 × 6000 — почти полгигабайта на один запрос.
MAX_PIXELS = 4096 * 4096
# То, что окно разрешает выбрать (accept="image/png,image/jpeg,image/webp,image/gif").
# MPO — JPEG с телефона с несколькими кадрами, Pillow называет его так.
FORMATS = ('JPEG', 'MPO', 'PNG', 'WEBP', 'GIF')
QUALITY = 90

NOT_AN_IMAGE = 'Не удалось прочитать фото — подойдёт JPG, PNG или WEBP'
TOO_BIG = 'Фото слишком большое — уменьшите его и загрузите снова'


class AvatarImageError(ValueError):
    """Фото не подходит — 400 с этим текстом."""


def _has_alpha(image):
    # У PNG с палитрой, а также у серого и цветного PNG с блоком tRNS
    # прозрачность записана цветом в info['transparency'], а не каналом.
    return image.mode in ('RGBA', 'LA', 'PA') or 'transparency' in image.info


def avatar_to_webp(data):
    """bytes загруженного фото -> bytes WebP. Не картинка — AvatarImageError."""
    # Импорт внутри: без Pillow отказывает только загрузка фото, а не весь
    # раздел профиля (Blueprint собирается в bot_schedule2 при старте).
    from PIL import Image, ImageOps

    if not data:
        raise AvatarImageError('Файл пустой — выберите фото ещё раз')
    try:
        image = Image.open(BytesIO(data))
        image_format = image.format
        width, height = image.size
    except Image.DecompressionBombError:
        # Сотни мегапикселей Pillow отбивает ещё при открытии, до MAX_PIXELS ниже.
        raise AvatarImageError(TOO_BIG) from None
    except Exception:  # noqa: BLE001 — любая ошибка разбора: это не картинка
        raise AvatarImageError(NOT_AN_IMAGE) from None
    if image_format not in FORMATS:
        raise AvatarImageError(NOT_AN_IMAGE)
    if width * height > MAX_PIXELS:
        raise AvatarImageError(TOO_BIG)
    try:
        if image_format in ('JPEG', 'MPO'):
            # JPEG распаковывается сразу уменьшенным (кратно 2), а не целиком.
            image.draft('RGB', (MAX_SIDE * 2, MAX_SIDE * 2))
        image = ImageOps.exif_transpose(image)
        if image.mode == 'I' or image.mode.startswith('I;16'):
            # 16-битный серый PNG: convert('RGB') обрезал бы всё ярче 255 в белый.
            image = image.convert('I').point(lambda value: value * (1 / 256)).convert('L')
        # Прозрачность сохраняется (WebP её держит); у PNG с палитрой она
        # записана номером цвета, а не каналом — отсюда _has_alpha.
        image = image.convert('RGBA' if _has_alpha(image) else 'RGB')
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        out = BytesIO()
        image.save(out, 'WEBP', quality=QUALITY, method=6)
    except Exception:  # noqa: BLE001 — битый файл всплывает при распаковке
        raise AvatarImageError('Не удалось прочитать фото — файл повреждён') from None
    return out.getvalue()
