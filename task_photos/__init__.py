# -*- coding: utf-8 -*-
"""Фотографии во вложениях задач: WebP при загрузке, миниатюра, адреса для показа.

ЗАЧЕМ. Постановку задачи часто иллюстрируют скриншотом или снимком из Telegram,
а до сих пор любой файл в карточке был кнопкой «скачать»: чтобы посмотреть
картинку, её приходилось сохранять на диск и открывать отдельно. Теперь
картинки показываются в карточке плитками и открываются во весь экран.

ЧТО ЗДЕСЬ ЕСТЬ. Только байты и адреса: перевод в WebP, миниатюра, путь
миниатюры в бакете и подписанные ссылки. Загрузку в бакет и SQL делает
bot_schedule2.py (_upload_task_attachments_to_gcs) и database.py — там же, где
жили вложения задач и раньше. Модуль не импортирует ни Flask, ни базу, поэтому
тест проверяет его целиком.

WEBP — ВСЕГДА, если кадр вообще открылся (решение владельца 24.09.2026: «фотки
сохранять в WebP»). Перевод берётся готовый из wiki/images.py — поворот из
EXIF, ICC-профиль, прозрачность палитры, потолок пикселей там уже решены, и
второй экземпляр этой логики разошёлся бы с первым молча. Отличий два.
keep_smaller=False: главный источник картинок в задачах — снимки, сохранённые
из Telegram, то есть уже пережатый JPEG; правило вики «вышло тяжелее — оставь
исходник» оставляло бы на них JPEG почти всегда, и правило владельца
выполнялось бы только на скриншотах. max_side=None: переведённый файл и есть
вложение — его скачивают, — и ужимать его под колонку статьи нельзя.

ПЕРЕВОДЯТСЯ ТОЛЬКО ФОТО И СКРИНШОТЫ (CONVERT_TYPES), а не всё, что Pillow
умеет открыть. Исходник после перевода не хранится, поэтому перевод обязан
ничего не терять, кроме байтов: у PSD пропали бы слои, у многостраничного
TIFF-скана — все страницы, кроме первой, у PNG из draw.io и Excalidraw —
встроенная схема, без которой его уже не отредактировать. Такие файлы, как и
всё, что Pillow не открыл (HEIC без плагина, подпись image/* у не-картинки), и
кадр больше 25 мегапикселей (память Render), уходят как есть: вложение задачи —
прежде всего файл, а плитка — удобство.

МИНИАТЮРА — ради плитки. Плитка в карточке 76 пикселей, а полный кадр хранится
в исходном разрешении; шесть снимков с телефона по 300–600 КБ ради шести
плиток — два-три мегабайта на каждое открытие карточки. Миниатюра в 480 пикселей весит 15–25 КБ. Кадр
меньше 480 миниатюрой служит сам (её нет, и адрес плитки равен полному).

АДРЕСА ПОДПИСАННЫЕ, а не прокси через API. Фронт живёт на другом домене, и
<img> не умеет слать заголовок авторизации; прокси означал бы N запросов через
Flask на карточку. Подпись — один запрос на карточку, а байты браузер берёт
прямо из бакета. Тот же приём, что у «Посылок» (parcels/photos.py).
"""

import io
import logging
import os
import re
import time
from datetime import timedelta

from wiki import images as wiki_images

# Что показываем плиткой. Список намеренно узкий и совпадает с фронтом
# (src/components/tasks/taskPhotos.js: PHOTO_PREVIEW_TYPES): расхождение
# выглядело бы как «плитка есть, а картинки в ней нет». HEIC нет — Chrome его не
# рисует; SVG нет — это документ, а не снимок. Новые фото после этой правки
# приходят сюда уже как image/webp; JPEG и PNG в списке — ради загруженных раньше.
PREVIEW_TYPES = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')

# Что переводим в WebP: снимки и скриншоты. Правило владельца — про «фотки», а
# не про документы в формате картинки (см. шапку: PSD, TIFF-скан, схема
# draw.io). pjpeg и x-png — старые имена тех же форматов, их ещё шлют браузеры.
CONVERT_TYPES = ('image/jpeg', 'image/pjpeg', 'image/png', 'image/x-png', 'image/apng',
                 'image/webp', 'image/gif', 'image/bmp')

# Ключи текстовых блоков PNG, в которых редактор хранит исходник схемы:
# draw.io пишет «mxfile» (старые версии — «mxGraphModel»), Excalidraw — свой
# JSON. Перевод в WebP их выбросил бы.
_EDITABLE_PNG_KEYS = ('mxfile', 'mxGraphModel', 'excalidraw', 'application/vnd.excalidraw+json')
_EDITABLE_PNG_SUFFIXES = ('.drawio.png', '.excalidraw.png')

# Тип по расширению — когда клиент не назвал его сам. CLI задач
# (scripts/task_board.py --attach) и часть программ шлют файл как
# application/octet-stream, и без этой таблицы снимок проехал бы мимо WebP.
# Своя таблица, а не mimetypes: там состав зависит от версии Python и от
# системных файлов сервера.
_EXT_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp',
}
_OPAQUE_TYPES = ('', 'application/octet-stream', 'binary/octet-stream')

# Сторона миниатюры. 480, а не 76: плитка рисуется и на экране с двойной
# плотностью, и шире на телефоне (сетка в четыре колонки), а пережать уже
# уменьшенное нельзя. Миниатюра КВАДРАТНАЯ, с обрезкой по центру, — ровно то, что
# показывает плитка (object-fit: cover). Вписанная по длинной стороне, она у
# длинного скриншота вышла бы полоской 62×480, и плитка растянула бы её в мыло.
THUMB_SIDE = 480

# Миниатюра всегда с потерями: в 480 пикселях мелкий текст скриншота всё равно
# не читается, за ним открывают полный кадр.
THUMB_QUALITY = 80

# Сколько живёт подписанная ссылка и когда её пора переподписать. Связка с
# фронтом жёсткая: он держит ответ 45 минут (taskPhotos.js:
# PHOTO_PREVIEW_TTL_MS), значит любая ВЫДАННАЯ ссылка обязана жить дольше. Кэш
# подписей ниже отдаёт старую ссылку, пока до её конца больше RESIGN_BEFORE, —
# то есть выданная ссылка живёт не меньше 60 минут, а в кэше фронта пролежит не
# больше 45. Страж — tests/test_task_photos.py.
SIGNED_MINUTES = 120
RESIGN_BEFORE_MINUTES = 60


def photo_kind(filename, content_type):
    """Тип снимка из CONVERT_TYPES ('image/png' …) или None — файл не переводим."""
    kind = str(content_type or '').strip().lower().split(';')[0].strip()
    if kind in _OPAQUE_TYPES:
        kind = _EXT_TYPES.get(os.path.splitext(str(filename or ''))[1].lower(), '')
    return kind if kind in CONVERT_TYPES else None


def _is_editable_diagram(data, filename, kind):
    """PNG, внутри которого лежит исходник схемы (draw.io, Excalidraw)."""
    if str(filename or '').lower().endswith(_EDITABLE_PNG_SUFFIXES):
        return True
    if kind not in ('image/png', 'image/x-png', 'image/apng'):
        return False
    pil = _pil()
    if not pil:
        return False
    try:
        with pil[0].open(io.BytesIO(data)) as probe:
            if probe.width * probe.height > wiki_images.MAX_PIXELS:
                return False  # конвертер такой кадр не возьмёт — распаковывать незачем
            probe.load()      # текстовые блоки после IDAT видны только так
            keys = set(getattr(probe, 'text', {}) or {}) | set(probe.info or {})
    except Exception:
        return False
    return any(key in keys for key in _EDITABLE_PNG_KEYS)


def webp_file_name(original_name):
    """Имя вложения после перевода: «IMG_1234.jpg» → «IMG_1234.webp».

    Берём ИСХОДНОЕ имя, а не то, что уже прошло secure_filename: тот выбрасывает
    кириллицу, и «фото.jpg» доезжает до базы как «jpg» — после перевода вышло бы
    «jpg.webp». Нелатинское имя становится «photo.webp».
    """
    base = os.path.splitext(os.path.basename(str(original_name or '').replace('\\', '/')))[0]
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', base).strip('._')[:120]
    return (safe or 'photo') + '.webp'


def thumb_blob_path(blob_path):
    """Путь миниатюры рядом с полным кадром: …/abc_photo.webp → …/abc_photo_thumb.webp."""
    root, _ext = os.path.splitext(str(blob_path or ''))
    return f'{root}_thumb.webp'


def _pil():
    """(Image, ImageOps) или None. Импорт отложенный — как в wiki/images.py:
    без Pillow вложение должно просто уйти как есть, а не уронить загрузку."""
    try:
        from PIL import Image, ImageOps
    except ImportError:      # pragma: no cover — окружение без Pillow
        return None
    return Image, ImageOps


def make_thumb(data):
    """Квадратная миниатюра WebP: (байты, ширина, высота) или None.

    На вход — уже переведённый кадр (prepare), поэтому здесь нет развилок по
    форматам: кадр открыт Pillow, влез в потолок пикселей и лежит в RGB/RGBA.
    exif_transpose всё же нужен: WebP, пришедший готовым, конвертер пропускает
    насквозь, и поле поворота у такого файла может быть своё.
    """
    pil = _pil()
    if not pil or not data:
        return None
    Image, ImageOps = pil
    try:
        with Image.open(io.BytesIO(data)) as src:
            if max(src.size) <= THUMB_SIDE:
                return None
            img = ImageOps.exif_transpose(src) or src
            icc = img.info.get('icc_profile')
            if img.mode not in ('RGB', 'RGBA'):
                has_alpha = 'A' in img.getbands() or 'transparency' in img.info
                img = img.convert('RGBA' if has_alpha else 'RGB')
            side = min(THUMB_SIDE, min(img.size))
            size = (side, side)
            small = ImageOps.fit(img, size, getattr(Image, 'Resampling', Image).LANCZOS,
                                 centering=(0.5, 0.5))
            options = {'quality': THUMB_QUALITY, 'method': 4}
            if icc:
                options['icc_profile'] = icc
            out = io.BytesIO()
            small.save(out, format='WEBP', **options)
            return out.getvalue(), size[0], size[1]
    except Exception:
        logging.info('Задачи: миниатюра не собралась (%d байт)', len(data or b''))
        return None


def prepare(data, *, filename, content_type):
    """Вложение-картинка → то, что ляжет в бакет. None — файл уходит как есть.

    Возвращает {'data', 'content_type', 'file_name', 'thumb'}: байты WebP, его
    тип, новое имя и байты миниатюры (или None). Без обращений к сети.
    """
    kind = photo_kind(filename, content_type)
    if not kind or not data or _is_editable_diagram(data, filename, kind):
        return None
    converted = wiki_images.to_webp(data, kind, keep_smaller=False, max_side=None)
    if not converted:
        return None
    out, out_kind, _width, _height = converted
    if out_kind != 'image/webp':
        # С keep_smaller=False конвертер исходник не возвращает; ветка — на
        # случай, если это правило в wiki/images.py когда-нибудь поменяют.
        return None
    thumb = make_thumb(out)
    return {
        'data': out,
        'content_type': 'image/webp',
        'file_name': webp_file_name(filename),
        'thumb': thumb[0] if thumb else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Подписанные адреса
# ─────────────────────────────────────────────────────────────────────────────

# Уже выданные подписи: {(bucket, path): (адрес, до когда по time.monotonic)}.
# Кэш ради КЭША БРАУЗЕРА: подпись v4 кладёт в адрес момент подписания, и без
# кэша карточка, открытая второй раз, качала бы все плитки заново. Пока адрес
# совпадает байт в байт, <img> берёт картинку из кэша.
_SIGNED = {}
_SIGNED_MAX = 2000


def _signed_url(client_of, bucket_name, blob_path, content_type, minutes):
    key = (bucket_name, blob_path)
    now = time.monotonic()
    cached = _SIGNED.get(key)
    if cached and cached[1] - now > RESIGN_BEFORE_MINUTES * 60:
        return cached[0]
    client = client_of()
    if client is None:
        return None
    try:
        url = client.bucket(bucket_name).blob(blob_path).generate_signed_url(
            version='v4',
            expiration=timedelta(minutes=minutes),
            method='GET',
            response_disposition='inline',
            response_type=content_type or 'image/webp',
        )
    except Exception:
        logging.warning('Задачи: подпись не собралась для %s/%s', bucket_name, blob_path,
                        exc_info=True)
        return None
    if len(_SIGNED) >= _SIGNED_MAX:
        for stale, (_url, until) in list(_SIGNED.items()):
            if until <= now:
                _SIGNED.pop(stale, None)
        if len(_SIGNED) >= _SIGNED_MAX:
            _SIGNED.clear()
    _SIGNED[key] = (url, now + minutes * 60)
    return url


def sign_previews(client_getter, rows, minutes=SIGNED_MINUTES):
    """Строки вложений → [{id, url, thumb_url}] для плиток карточки.

    Строка, которую подписать не вышло, в ответ не попадает: фронт покажет её
    обычной кнопкой файла, а не пустой плиткой. Путь в бакете наружу не отдаём.
    Клиент хранилища строится один раз на вызов и только если понадобился: на
    карточке, все адреса которой уже в кэше, он не нужен вовсе, а его сборка —
    это разбор приватного ключа.
    """
    held = []

    def client_of():
        if not held:
            try:
                held.append(client_getter() if callable(client_getter) else None)
            except Exception:
                logging.warning('Задачи: клиент хранилища недоступен', exc_info=True)
                held.append(None)
        return held[0]

    out = []
    for row in rows or []:
        bucket_name = str(row.get('gcs_bucket') or '').strip()
        blob_path = str(row.get('gcs_blob_path') or '').strip()
        if not bucket_name or not blob_path:
            continue
        url = _signed_url(client_of, bucket_name, blob_path, row.get('content_type'), minutes)
        if not url:
            continue
        thumb_path = str(row.get('thumb_blob_path') or '').strip()
        thumb_url = (_signed_url(client_of, bucket_name, thumb_path, 'image/webp', minutes)
                     if thumb_path else None)
        out.append({'id': row.get('id'), 'url': url, 'thumb_url': thumb_url or url})
    return out
